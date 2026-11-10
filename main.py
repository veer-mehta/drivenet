import getpass
import os
import sys

from dotenv import load_dotenv

from account_manager import AUTH_PORT, CLIENT_ID, AccountManager
from storage_manager import Session, VaultError, VaultLocked, VaultNotFound, WrongPassword

load_dotenv()

MENU = (
    "\n\n1> accounts\t\t2> view tree\t\t3> upload files\t\t4> upload folder\n"
    "5> download file\t6> download folder\t7> delete file\t\t8> delete folder\n"
    "9> search\t\t10> change password\t11> settings\t\t12> quit\n>> "
)


def new_password():
    first = getpass.getpass("new password (10+ characters): ")
    if len(first) < 10:
        raise ValueError("use at least 10 characters")
    if first != getpass.getpass("repeat: "):
        raise ValueError("passwords differ")
    return first


def ask_upload_options(session):
    drives = session.state()["accounts"]
    encrypt = input("encrypt? (Y/n): ").strip().lower() != "n"
    chosen = None
    if len(drives) > 1:
        for number, email in enumerate(drives, 1):
            print(f"  {number}> {email}")
        raw = input("store on which drives? (enter for all, or numbers like 1,3): ").replace(" ", "")
        if raw:
            try:
                picks = [int(part) for part in raw.split(",") if part]
            except ValueError:
                picks = []
            if not picks or any(number < 1 or number > len(drives) for number in picks):
                raise ValueError("pick numbers from the list")
            chosen = [drives[number - 1] for number in dict.fromkeys(picks)]
    if not encrypt:
        print("warning: not encrypted. anyone with access to those google accounts can read it.")
    return chosen, encrypt


def connect_missing(session, accounts):
    for email in session.state()["accounts"]:
        while not accounts.is_connected(email):
            if input(f"press enter to sign in as {email} (or type skip)... ").strip().lower() == "skip":
                break
            try:
                accounts.connect(email)
            except Exception as error:
                print(f"error: {error}")


def unlock(session, accounts, email):
    created = False
    while True:
        password = getpass.getpass("vault password: ")
        try:
            session.open(password, [email], begin=False)
            break
        except WrongPassword:
            print("wrong password")
        except VaultLocked as error:
            print(error)
            sys.exit(1)
        except VaultNotFound:
            print(f"no vault under {email}")
            if input("create one? (y/N) ").strip().lower() != "y":
                continue
            print("there is no recovery. forget the password and the files are gone.")
            try:
                session.create(new_password(), [email])
                created = True
                break
            except ValueError as error:
                print(error)
        except VaultError as error:
            print(f"error: {error}")
            sys.exit(1)
    state = session.state()
    print(f"vault open: {len(state['files'])} files, {len(state['accounts'])} accounts")
    connect_missing(session, accounts)
    if not created:
        try:
            session.begin()
        except VaultError as error:
            print(error)
            sys.exit(1)


def manage_accounts(session, accounts):
    state = session.state()
    for index, email in enumerate(state["accounts"]):
        status = "connected" if accounts.is_connected(email) else "not connected"
        print(f"[{index}] {email}\t{state['usage'].get(email, 0)} blocks\t{status}")
    match input("1> add account\t2> remove account\t3> reconnect\t4> back\n>> "):
        case "1":
            email = accounts.connect()
            session.add_account(email)
            print(f"added {email}")
        case "2":
            email = input("account email: ").strip()
            held = session.state()["usage"].get(email, 0)
            migrate = False
            if held:
                migrate = input(f"{email} holds {held} blocks. move them to the other drives? (y/N): ").strip().lower() == "y"
                if migrate:
                    connect_missing(session, accounts)
            session.remove_account(email, migrate)
            print(f"removed {email}")
        case "3":
            connect_missing(session, accounts)


def search(session):
    query = input("enter search query (or pattern): ").strip()
    use_regex = input("use regex? (y/n, default n): ").strip().lower() == "y"
    exact = input("exact name match? (y/n, default n): ").strip().lower() == "y"
    print("\nshow:\n1> everything (default)\n2> files only\n3> folders only")
    filter_type = {"2": "file", "3": "dir"}.get(input(">> ").strip())
    results = session.search(query, exact=exact, use_regex=use_regex, filter_type=filter_type)
    if not results:
        print(f"\nnothing found for '{query}'")
        return
    print(f"\nfound {len(results)} item(s):")
    print(f"{'TYPE':<6} {'SIZE (bytes)':<14} PATH")
    for item in results:
        size = f"{item['size']:,}" if item["type"] == "file" else "-"
        print(f"{item['type'].upper():<6} {size:<14} {item['path']}")


SETTINGS = {"1": ("block_mb", "block size in mb"), "2": ("transfers", "transfers per drive"), "3": ("kdf", "hardening")}


def settings(session, accounts):
    state = session.state()
    current, limits = state["settings"], state["limits"]
    print(f"block size {current['block_mb']} mb, transfers {current['transfers']}, hardening {current['kdf']}")
    for item in state["unfinished"]:
        print(f"unfinished: {item['path']}  {item['done']} of {item['total']} blocks saved (upload it again to continue)")
    match input("1> block size\t2> transfers\t3> hardening\t4> discard unfinished\t5> clean up\t6> back\n>> "):
        case "1" | "2" | "3" as pick:
            key, label = SETTINGS[pick]
            low, high = limits[key]
            try:
                raw = input(f"{label} ({low} to {high}): ")
                value = float(raw) if key == "block_mb" else int(raw)
            except ValueError:
                raise ValueError("enter a number") from None
            session.set_settings({key: value})
            print("saved")
        case "4":
            session.discard_upload(input("remote path of the unfinished upload: "))
            print("discarded")
        case "5":
            connect_missing(session, accounts)
            print(f"removed {session.clean_orphans()} unused blocks")


def run_command(session, accounts, choice):
    match choice:
        case "1":
            manage_accounts(session, accounts)
        case "2":
            print("\n" + "\n".join(session.tree_lines()))
        case "3":
            connect_missing(session, accounts)
            local, remote = input("enter local path: "), input("enter remote path: ")
            drives, encrypt = ask_upload_options(session)
            session.upload_file(local, remote, drives, encrypt)
            print("upload successful")
        case "4":
            connect_missing(session, accounts)
            local, remote = input("enter local path: "), input("enter remote path: ")
            drives, encrypt = ask_upload_options(session)
            count = session.upload_folder(local, remote, drives, encrypt)
            print(f"upload successful ({count} files)")
        case "5":
            connect_missing(session, accounts)
            session.download_file(input("enter remote path: "), input("save as (blank = same name): "))
            print("download successful")
        case "6":
            connect_missing(session, accounts)
            remote_path = "/" if input("download root folder? (y/n): ").lower() == "y" else input("enter remote path: ")
            count = session.download_folder(remote_path, input("enter local folder: "))
            print(f"download successful ({count} files)")
        case "7":
            connect_missing(session, accounts)
            session.delete_file(input("enter remote path: "))
            print("deletion successful")
        case "8":
            connect_missing(session, accounts)
            session.delete_folder(input("enter remote path: "))
            print("deletion successful")
        case "9":
            search(session)
        case "10":
            session.change_password(new_password())
            print("password changed")
        case "11":
            settings(session, accounts)
        case _:
            print("invalid option")


def repl(session, accounts):
    while True:
        choice = input(MENU).strip()
        if choice == "12":
            print("quitting")
            return
        try:
            run_command(session, accounts, choice)
        except Exception as error:
            print(f"error: {error}")


def main():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not CLIENT_ID.match(client_id):
        print("set GOOGLE_CLIENT_ID in .env to the same google client id the website uses.")
        print(f"then add http://localhost:{AUTH_PORT} as an authorized javascript origin for it in google cloud.")
        sys.exit(1)

    accounts = AccountManager(client_id)
    print("sign in with the google account that holds your vault")
    email = accounts.connect()
    session = Session(accounts.token)
    try:
        unlock(session, accounts, email)
        repl(session, accounts)
    except KeyboardInterrupt:
        print()
    finally:
        session.close()


if __name__ == "__main__":
    main()
