import os
import re
from datetime import datetime
import piexif
from PIL import Image, PngImagePlugin
from concurrent.futures import ThreadPoolExecutor, as_completed


def extract_date_from_title(filename):
    """
    Extract date and time from the image filename.
    Supported formats:
    - PXL_YYYYMMDD_HHMMSS
    - IMG_YYYYMMDD_HHMMSS
    - Screenshot_YYYYMMDD-HHMMSS
    - IMG-YYYYMMDD-WAXXXX
    """
    patterns = [
        r"PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
        r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
        r"Screenshot_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})",
        r"IMG-(\d{4})(\d{2})(\d{2})-WA\d+\.jpg",
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match and len(match.groups()) == 6:  # Ensure all six groups are present
            year, month, day, hour, minute, second = map(int, match.groups())
            return datetime(year, month, day, hour, minute, second)
    return None


def update_metadata_jpeg(image_path, new_datetime):
    """
    Update metadata for JPEG images.
    """
    try:
        exif_dict = piexif.load(image_path)
        existing_datetime = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)

        if existing_datetime:
            existing_datetime = datetime.strptime(existing_datetime.decode("utf-8"), "%Y:%m:%d %H:%M:%S")
            if existing_datetime == new_datetime:
                return "✅ Already matches"

        new_datetime_str = new_datetime.strftime("%Y:%m:%d %H:%M:%S")
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = new_datetime_str.encode("utf-8")
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = new_datetime_str.encode("utf-8")
        exif_bytes = piexif.dump(exif_dict)

        image = Image.open(image_path)
        image.save(image_path, exif=exif_bytes)
        return "🛠️ Updated"
    except Exception as e:
        return f"❌ Failed: {e}"


def update_metadata_png(image_path, new_datetime):
    """
    Update metadata for PNG images with a creation time field.
    """
    try:
        image = Image.open(image_path)
        metadata = image.info  # Read existing metadata
        new_datetime_str = new_datetime.strftime("%Y-%m-%d %H:%M:%S")

        # If existing metadata already has the same datetime, skip updating
        if "Creation Time" in metadata and metadata["Creation Time"] == new_datetime_str:
            return "✅ Already matches"

        # Add/Update the creation time metadata
        png_metadata = PngImagePlugin.PngInfo()
        png_metadata.add_text("Creation Time", new_datetime_str)

        image.save(image_path, "png", pnginfo=png_metadata)
        return "🛠️ Updated"
    except Exception as e:
        return f"❌ Failed: {e}"


def process_file(file_path, folder_path):
    """
    Process a single file to update its metadata.
    """
    relative_path = os.path.relpath(file_path, folder_path)
    new_datetime = extract_date_from_title(os.path.basename(file_path))

    if not new_datetime:
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

    print("\n📊 Summary:")
    print(f"✔️ Processed: {successful}/{total_files}")
    if failed_images:
        print("\n❌ Failed Files:")
        for failed_file, reason in failed_images:
            print(f" - {failed_file}: {reason}")


if __name__ == "__main__":
    folder_path = input("📂 Enter the path to the folder containing images: ").strip()
    if os.path.isdir(folder_path):
        process_images(folder_path, max_threads=8)
    else:
        print("❌ Invalid folder path.")
