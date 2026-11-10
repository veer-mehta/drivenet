# drivenet

Splits files into encrypted blocks and spreads them across several Google Drive accounts, so you can use their combined storage as one private drive. It runs as a website and as a command line tool, and both read and write the same vault, so either one can open anything the other stored.

## How it works

- Files are split into blocks (5 MB by default) and encrypted with AES-256-GCM before they leave your machine. Each file has its own key, and every block is authenticated against its file and position, so a tampered, swapped or reordered block is rejected.
- An unfinished upload is journaled in the vault. If it stops for any reason, upload the same file again and it continues from the last saved block.
- Deletes are journaled too, and a clean up action removes blocks on your drives that no file refers to.
- Blocks are spread round-robin across your Google accounts and stored in each account's hidden app data folder.
- A small encrypted vault holds the account list and the file index. A copy lives in every account, so losing one account does not lose the index.
- The vault is locked with your password (Scrypt). Your files are encrypted with a random master key that the vault stores wrapped by that password, so changing the password never re-encrypts your data.
- The password is asked once per device and is never stored, sent or read by anything. Signing in with Google alone does not open your files. After the first unlock the vault key is kept on that device, wrapped by a non-extractable WebCrypto key in IndexedDB, for the time you pick in the settings (this tab, 1, 7 or 30 days, or off), so refreshes, new tabs and restarts show a continue with google button instead of a password box. Lock and logout clear it, and a new device or browser asks for the password again. The Google token lasts an hour and is refreshed on your clicks while you use the app.
- Only one session can open the vault at a time. A session keeps a short lease on it and releases it when you lock or quit.
- The website runs entirely in your browser. Python runs inside the page, and the only servers it talks to are Google's.

## Setup

Both programs sign in with the same Google client ID. Create one in the Google Cloud console:

1. Create a project and enable the Google Drive API.
2. On the OAuth consent screen choose External, add yourself as a test user, and add the scope `https://www.googleapis.com/auth/drive.appdata`.
3. Create an OAuth client ID of type Web application.
4. Add these authorized JavaScript origins:
   - `http://localhost:8000` for the website
   - `http://localhost:8765` for the command line tool
5. Copy `example.env` to `.env` and set `GOOGLE_CLIENT_ID` to your client ID.

```bash
pip install -r requirements.txt
```

## Website

```bash
python dev_server.py
```

Open `http://localhost:8000`, sign in with Google and unlock or create your vault. To host it yourself, serve the `web/` folder, provide a `config.js` that sets `window.APP_CONFIG = { clientId: "..." }`, and send the security headers listed at the top of `dev_server.py`.

## Command line

```bash
python main.py
```

It opens your browser to sign in with Google, asks for your vault password, then shows a menu:

```
1> accounts        2> view tree        3> upload files      4> upload folder
5> download file   6> download folder  7> delete file       8> delete folder
9> search          10> change password 11> settings         12> quit
```

Option 11 opens the settings: block size, transfers per drive and password hardening are stored in the vault, so the website and the command line share them. Remote paths look like `/folder/file.txt`. Each account in the vault signs in once per session, and uploads and downloads use all of them in parallel.

## Layout

```
web/            the website
web/core/       the engine, shared by the website and the command line tool
main.py         command line menu
storage_manager.py   connects the command line tool to the engine
account_manager.py   browser sign-in for the command line tool
dev_server.py   local web server
```
