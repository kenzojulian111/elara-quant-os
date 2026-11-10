import os

# DAFTAR BLOKIR (Folder yang HARAM disentuh)
IGNORE_DIRS = {
    "venv",
    ".venv",
    "env",
    "virtualenv",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "brain_storage",
    "backups",
    "documents",
    "node_modules",
    "Lib",
    "site-packages",
    "Scripts",
    "Include",
    "log",
    "json",
}

SELF_FILE = os.path.basename(__file__)
OUTPUT_FILE = "ELARA_FULL_CODE.txt"
IGNORE_FILES = {
    SELF_FILE,
    OUTPUT_FILE,
    ".env",
    ".env.docker",
    "package-lock.json",
    "pnpm-lock.yaml",
}
EXTENSIONS = {".py", ".html", ".css", ".js", ".yml", ".yaml", ".ini", ".toml", ".md"}


def merge_files():
    output_file = OUTPUT_FILE

    # Hapus file lama biar bersih
    if os.path.exists(output_file):
        os.remove(output_file)

    count = 0

    with open(output_file, "w", encoding="utf-8") as outfile:
        for root, dirs, files in os.walk("."):
            # 1. Filter Folder: Buang folder yang ada di IGNORE_DIRS
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            # 2. Filter Tambahan: Cek apakah path saat ini mengandung kata 'site-packages'
            if "site-packages" in root or "dist-packages" in root:
                continue

            for file in files:
                # 3. Filter File: Cek ekstensi
                if not any(file.endswith(ext) for ext in EXTENSIONS):
                    continue

                # 4. Filter Self: Jangan masukkan script ini sendiri atau outputnya
                if file in IGNORE_FILES:
                    continue

                file_path = os.path.join(root, file)

                # Tulis Header
                outfile.write(f"\n{'=' * 50}\n")
                outfile.write(f"FILE: {file_path}\n")
                outfile.write(f"{'=' * 50}\n")

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as infile:
                        outfile.write(infile.read())
                    print(f"[OK] Added: {file_path}")
                    count += 1
                except Exception as e:
                    print(f"[ERR] Error reading {file_path}: {e}")
                    outfile.write(f"Error reading file: {e}")


if __name__ == "__main__":
    merge_files()

