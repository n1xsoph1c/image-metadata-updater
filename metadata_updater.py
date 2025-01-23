import os
import re
from datetime import datetime
import piexif
from PIL import Image, PngImagePlugin
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


def extract_date_from_title(filename, file_path=None):
    """
    Extract date and time from the image filename.
    Supported formats:
    - PXL_YYYYMMDD_HHMMSS (Google Pixel format)
    - IMG_YYYYMMDD_HHMMSS (Generic format)
    - Screenshot_YYYYMMDD-HHMMSS (ScreenShot format)
    - IMG-YYYYMMDD-WAXXXX or VID-YYYYMMDD-WAXXXX (WhatsApp format, assumes AM time)
    - FB_IMG_* (Facebook format, fallback to file's last modified time)
    - LRM_YYYYMMDD_HHMMSS (Lightroom format)
    """
    patterns = [
        r"PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
        r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
        r"Screenshot_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})",
        r"(IMG|VID)-(\d{4})(\d{2})(\d{2})-WA\d+",  # WhatsApp format (IMG/VID-YYYYMMDD-WAXXXX)
        r"LRM_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",  # Lightroom format (LRM_YYYYMMDD_HHMMSS)
    ]

    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            if len(match.groups()) == 6:  # Formats with time
                year, month, day, hour, minute, second = map(int, match.groups()[:6])
                return datetime(year, month, day, hour, minute, second)
            elif len(match.groups()) == 4:  # WhatsApp format
                _, year, month, day = match.groups()
                return datetime(int(year), int(month), int(day), 0, 0, 0)  # Assume 00:00:00 AM for WhatsApp files

    # Handle Facebook filenames (FB_IMG_*)
    if filename.startswith("FB_IMG_"):
        # Fallback to the file's last modified time if no date is present in the filename
        if file_path and os.path.exists(file_path):
            timestamp = os.path.getmtime(file_path)
            return datetime.fromtimestamp(timestamp)

    # Handle Snapchat filenames (unsupported)
    if filename.lower().startswith("snapchat-"):
        return None

    return None


def update_metadata_jpeg(image_path, new_datetime):
    """
    Update metadata for JPEG images.
    """
    try:
        # Load existing EXIF metadata
        exif_dict = piexif.load(image_path)

        # Format the new datetime as a string
        new_datetime_str = new_datetime.strftime("%Y:%m:%d %H:%M:%S")

        # Check existing DateTimeOriginal in EXIF metadata
        existing_datetime = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
        if existing_datetime:
            existing_datetime = datetime.strptime(existing_datetime.decode("utf-8"), "%Y:%m:%d %H:%M:%S")
            if existing_datetime == new_datetime:
                # Check the filesystem's last modified and access time
                stat = os.stat(image_path)
                file_last_modified = datetime.fromtimestamp(stat.st_mtime)  # Last modified time
                file_last_access = datetime.fromtimestamp(stat.st_atime)  # Access time

                # Skip if all dates match
                if file_last_modified == new_datetime and file_last_access == new_datetime:
                    return "✅ Already matches"

        # Update EXIF metadata
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = new_datetime_str.encode("utf-8")
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = new_datetime_str.encode("utf-8")
        exif_bytes = piexif.dump(exif_dict)

        # Save the updated EXIF metadata to the file
        image = Image.open(image_path)
        image.save(image_path, exif=exif_bytes)

        # Update the file's last modified and access time
        os.utime(image_path, (new_datetime.timestamp(), new_datetime.timestamp()))

        return "🛠️ Updated"
    except Exception as e:
        return f"❌ Failed: {e}"




def update_metadata_png(image_path, new_datetime):
    """
    Update metadata for PNG images with a creation time field.
    """
    try:
        # Open the PNG image
        image = Image.open(image_path)
        metadata = image.info  # Read existing metadata
        new_datetime_str = new_datetime.strftime("%Y-%m-%d %H:%M:%S")

        # Check existing "Creation Time" metadata
        if "Creation Time" in metadata and metadata["Creation Time"] == new_datetime_str:
            # Check the filesystem's last modified and access time
            stat = os.stat(image_path)
            file_last_modified = datetime.fromtimestamp(stat.st_mtime)  # Last modified time
            file_last_access = datetime.fromtimestamp(stat.st_atime)  # Access time

            # Skip if all dates match
            if file_last_modified == new_datetime and file_last_access == new_datetime:
                return "✅ Already matches"

        # Add or update metadata
        png_metadata = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            # Ensure all metadata values are strings
            png_metadata.add_text(key, str(value))
        png_metadata.add_text("Creation Time", new_datetime_str)

        # Save the updated PNG image with metadata
        image.save(image_path, "png", pnginfo=png_metadata)

        # Update the file's last modified and access time
        os.utime(image_path, (new_datetime.timestamp(), new_datetime.timestamp()))

        return "🛠️ Updated"
    except Exception as e:
        return f"❌ Failed: {e}"




def process_file(file_path, folder_path):
    """
    Process a single file to update its metadata.
    """
    relative_path = os.path.relpath(file_path, folder_path)
    new_datetime = extract_date_from_title(os.path.basename(file_path), file_path)

    if not new_datetime:
        if os.path.basename(file_path).lower().startswith("snapchat-"):
            return relative_path, "⚠️ Snapchat filenames unsupported for metadata updates"
        return relative_path, "⚠️ No valid date in filename"

    if file_path.lower().endswith((".jpg", ".jpeg")):
        status = update_metadata_jpeg(file_path, new_datetime)
    elif file_path.lower().endswith(".png"):
        status = update_metadata_png(file_path, new_datetime)
    else:
        status = "❌ Unsupported file type"

    return relative_path, status


def process_images(folder_path, max_threads=8):
    """
    Process all images in the folder and its subfolders using multithreading.
    """
    supported_extensions = (".jpg", ".jpeg", ".png")
    all_files = []

    # Gather all files
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith(supported_extensions):
                all_files.append(os.path.join(root, file))

    total_files = len(all_files)
    successful = 0
    failed_images = []

    print(f"🔄 Starting metadata updates for {total_files} files using {max_threads} threads...\n")
    start_time = time.time()  # Start timer

    # Process files using multithreading
    with ThreadPoolExecutor(max_threads) as executor:
        futures = {executor.submit(process_file, file, folder_path): file for file in all_files}

        for index, future in enumerate(as_completed(futures), 1):
            file_path = futures[future]
            try:
                relative_path, status = future.result()
                if "✅" in status or "🛠️" in status:
                    successful += 1
                    print(f"[✅] {index}/{total_files} [{relative_path}] - {status}")
                else:
                    failed_images.append((relative_path, status))
                    print(f"[❌] {index}/{total_files} [{relative_path}] - {status}")
            except Exception as e:
                relative_path = os.path.relpath(file_path, folder_path)
                failed_images.append((relative_path, f"❌ Unexpected error: {e}"))
                print(f"[❌] {index}/{total_files} [{relative_path}] - ❌ Unexpected error: {e}")

    # Calculate elapsed time
    elapsed_time = time.time() - start_time
    rate = total_files / elapsed_time if elapsed_time > 0 else 0
    
    if failed_images:
        print("\n\n❌ Failed Files:")
        for failed_file, reason in failed_images:
            print(f" - {failed_file}: {reason}")
    
    print("\n📊 Summary:")
    print(f"✔️ Processed: {successful}/{total_files}")
    print(f"⏱️ {elapsed_time:.2f} sec → {total_files} files @{rate:.2f} files/sec")
    


if __name__ == "__main__":
    folder_path = input("📂 Enter the path to the folder containing images: ").strip()
    if os.path.isdir(folder_path):
        process_images(folder_path, max_threads=8)
    else:
        print("❌ Invalid folder path.")
