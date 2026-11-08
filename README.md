# drivenet

A CLI tool that splits files into encrypted chunks and distributes them across multiple Google Drive accounts, effectively multiplying your available storage.

## How it works

- Files are split into fixed-size blocks (default 5 MB)
- Each block is encrypted with Fernet symmetric encryption before upload
- Chunks are distributed round-robin across all configured Google accounts
- A local `fs.json` tracks the virtual filesystem and chunk locations

## Setup

**1. Google Cloud credentials**

Create a project in [Google Cloud Console](https://console.cloud.google.com), enable the Drive API, and download `credentials.json` into the project root.

**2. Environment**

```bash
python -m venv gdenv
gdenv\Scripts\activate       # Windows
pip install -r requirements.txt
```

**3. `.env` file**

```env
SCOPE=https://www.googleapis.com/auth/drive
KEY_FILE=secret.key
BLOCK_SIZE=5 * 1024 * 1024
THREADS_PER_ACCOUNT=5
```

**4. Run**

```bash
python main.py
```

On first run, you'll be prompted to add a Google account via browser OAuth.

## Usage

```
1> manage accounts     2> view fs tree        3> upload files        4> upload folders
5> download file       6> download folder     7> delete file         8> delete folder
9> quit
```

- **Manage accounts** — add or remove Google Drive accounts
- **View fs tree** — print the virtual filesystem
- **Upload / Download / Delete** — operate on files and folders using remote paths like `/folder/file.txt`

## Notes

- `fs.json` is the filesystem index — do not delete it or you lose track of all uploaded chunks
- `secret.key` holds the encryption key — back it up; losing it means uploaded data is unrecoverable
- Adding more accounts increases total storage and upload/download parallelism