# -*- coding: utf-8 -*-
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

import pyperclip

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
)

# =========================
# YOUR SETTINGS (Hardcoded)
# =========================
EMAIL = "turneredward@gmail.com"
PASSWORD = "turneredward"
people_names = [
    "Paul Rees",
    "Jose Delgado",
    "Pawel Babut",
    "Peter Shaw",
    "Simona Bodis",
    "Andy Sowden",
    "Mark Gregory",
    "Sally Walker",
    "Ertan Ates",
    "Sharon White",
    "Sarah Upson",
    "Tim Roberts",
    "Jenny Pollard",
    "Chetan Ojha",
    "Becky Kernsworth",
    "Lisa Smith",
    "Kevin Caulfield",
    "Muhammad Qureshi",
    "Wendy Holt",
    "Rebecca Owen",
    "ARJUNAN C",
    "Keith Martin",
    "Millie Jones",
    "Emma Mitchell",
    "Jim Wheeler",
    "Stephen Percival",
    "Tabitha Atkinson",
    "Darya Tsybulko",
    "Rachel Hughes",
    "cindy kong",
    "Vijay Mohan",
    "Saeed Anjum",
    "Faisal A Farooqui",
    "Nicklas Folk",
    "Christopher Farley",
    "Julian Thomas",
    "John Pinnington",
    "Robert Ayres",
    "Alex W.H. Hsiao",
    "Patrick Killeen",
    "Gianluca Santori",
    "Umair Iqbal",
    "Kelly Chan",
    "Ruth Scholey-Jones",
    "Stoffer Dunnik",
    "Jie (Lisa) Li, CFA",
    "Divya Deep Sharma",
    "Mark Mitchell",
    "Andrew Wilson"
]


ATTACHMENT_PATH = None #r"C:\Users\prathamesh.khatavkar\Downloads\INNOOOVA Retail Deck.pdf"  # set to None for text-only

MESSAGE_TEMPLATE = """Hi {name},

We’re hiring an experienced IT Project Manager for an exciting global role within global logistics. This is a fully remote position, working closely with Product, Engineering and QA in an Agile environment to translate business needs into clear, delivery-ready requirements.

Strong analysis, documentation and stakeholder communication skills are essential. Experience with integrations, data flows or APIs is a plus.

If this sounds like you, drop me a message.

Ed
"""

# =========================
# CONFIG (selectors, waits)
# =========================
TIMEOUTS = {"page": 40, "ui": 30, "short": 10}
SLEEPS = {
    "pre_message_page": 4.0,
    "after_name_type": 5.0,
    "after_enter": 0.7,
    "after_click_editor": 0.3,
    "after_paste": 3.0,
    "after_attach": 3.0,
    "after_send": 3.0,
}

SELECTORS = {
    "login_username": [
        (By.CSS_SELECTOR, "input#username"),
        (By.CSS_SELECTOR, "input[name='session_key']"),
        (By.CSS_SELECTOR, "input#session_key"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
    ],
    "login_password": [
        (By.CSS_SELECTOR, "input#password"),
        (By.CSS_SELECTOR, "input[name='session_password']"),
        (By.CSS_SELECTOR, "input#session_password"),
        (By.CSS_SELECTOR, "input[autocomplete='current-password']"),
    ],
    "login_ok_any": [
        (By.ID, "global-nav-typeahead"),
        (By.CSS_SELECTOR, "a[href*='/messaging/']"),
        (By.CSS_SELECTOR, "input.search-global-typeahead__input"),
        (By.CSS_SELECTOR, ".global-nav__me"),
    ],
    "typeahead_input": [(By.CSS_SELECTOR, "input.msg-connections-typeahead__search-field")],
    "recipient_chip_any": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[contains(@class,'recipient')]"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[contains(@class,'pill')]"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[contains(@class,'chip')]"),
    ],
    # Editor fallbacks: exact class OR any contenteditable/role=textbox inside the New message form
    "editor": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//div[contains(@class,'msg-form__contenteditable') and @contenteditable='true']"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[(@contenteditable='true') or (@role='textbox')]"),
    ],
    "attach_button_any": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//button[contains(@aria-label,'Attach')]"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//button[contains(@aria-label,'file')]"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//button[contains(@aria-label,'Upload')]"),
    ],
    "file_input": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//input[@type='file']")
    ],
    "send_button": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//button[contains(@class,'msg-form__send-button') and not(@disabled)]")
    ],
}

# =========================
# Stop / shutdown
# =========================
STOP_FLAG = Path(__file__).resolve().parent / "sendline.stop"


class StopRequested(Exception):
    """User clicked Stop in the UI (or Ctrl+C)."""


def clear_stop_flag() -> None:
    try:
        STOP_FLAG.unlink(missing_ok=True)
    except OSError:
        pass


def should_stop() -> bool:
    return STOP_FLAG.is_file()


def interruptible_sleep(seconds: float) -> None:
    deadline = time.time() + seconds
    while True:
        if should_stop():
            raise StopRequested()
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def shutdown_browser() -> None:
    global driver
    print("Closing Chrome...")
    _kill_chrome()
    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass
    driver = None
    print("Chrome closed.")


# =========================
# Helpers
# =========================
def wait_for_any(driver: webdriver.Chrome, choices: Iterable[tuple[str, str]], cond, timeout: Optional[int] = None):
    timeout = timeout or TIMEOUTS["ui"]
    last_exc = None
    for by, sel in choices:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if should_stop():
                raise StopRequested()
            slice_timeout = min(1.0, max(0.05, deadline - time.time()))
            try:
                return WebDriverWait(driver, slice_timeout).until(cond((by, sel)))
            except TimeoutException as e:
                last_exc = e
    if last_exc:
        raise last_exc
    raise TimeoutException("wait_for_any: no selectors provided")

def scroll_into_view(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)

def click_js(driver, el):
    driver.execute_script("arguments[0].click();", el)

def editor_text_len(driver, el) -> int:
    return driver.execute_script("return (arguments[0].innerText || '').trim().length;", el)

def paste_message_via_clipboard(driver, el, text: str) -> bool:
    """
    Robust paste: copy to OS clipboard, Ctrl+V into editor, verify text exists.
    Returns True if editor has text after paste.
    """
    # Put text on OS clipboard
    pyperclip.copy(text)

    # Move focus & click editor
    scroll_into_view(driver, el)
    click_js(driver, el)
    interruptible_sleep(0.1)

    # Send Ctrl+V (real paste)
    actions = ActionChains(driver)
    actions.move_to_element(el).click(el).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
    interruptible_sleep(SLEEPS["after_paste"])

    # Nudge with a real keystroke so frameworks flip state
    try:
        el.send_keys(" ")
        el.send_keys(Keys.BACKSPACE)
    except Exception:
        pass

    # Check if editor now has visible text
    return editor_text_len(driver, el) > 0

def fallback_type_like_human(driver, el, text: str):
    """
    Slow, human-like typing as an ultimate fallback.
    """
    actions = ActionChains(driver)
    actions.move_to_element(el).click(el).perform()
    for chunk in text.split("\n"):
        if chunk:
            actions.send_keys(chunk).perform()
        actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT).perform()  # newline without sending
        interruptible_sleep(0.05)

# =========================
# Browser setup — copy Mike profile into a dedicated automation folder.
# Using the live Chrome "User Data" dir with a debug port often opens
# about:blank and never exposes port 9222. A copied profile works reliably.
# =========================
CHROME_USER_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
CHROME_PROFILE = "Profile 7"  # display name: Mike (muneshmyk@gmail.com)
AUTOMATION_USER_DATA = Path(__file__).resolve().parent / "chrome_automation_data"
DEBUG_HOST = "127.0.0.1"
DEBUG_PORT = 9222


# Set True only when you want to re-copy from Mike's Chrome profile.
# After you log into LinkedIn once in the automation window, leave this False
# so that session is kept (re-copying often drops the LinkedIn login).
RESYNC_FROM_MIKE_EACH_RUN = False


def _find_chrome_exe() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("Could not find chrome.exe")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, timeout: float = 45) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(host, port):
            return
        time.sleep(0.4)
    raise TimeoutException(f"Chrome debug port {host}:{port} did not open in time.")


def _kill_chrome() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
        capture_output=True,
        text=True,
        check=False,
    )
    time.sleep(2)


def _clear_profile_locks(folder: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock = folder / name
        try:
            if lock.exists() or lock.is_symlink():
                lock.unlink()
        except OSError:
            pass


def _list_chrome_profiles() -> list[tuple[str, str, str]]:
    """Return (folder, display_name, email) for profiles under Chrome User Data."""
    found: list[tuple[str, str, str]] = []
    local_state = CHROME_USER_DATA / "Local State"
    info: dict = {}
    if local_state.is_file():
        try:
            info = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {}) or {}
        except Exception:
            info = {}

    folders: list[str] = []
    if CHROME_USER_DATA.is_dir():
        for child in sorted(CHROME_USER_DATA.iterdir()):
            if child.is_dir() and (child.name == "Default" or child.name.startswith("Profile ")):
                folders.append(child.name)
    for folder in folders:
        meta = info.get(folder) or {}
        display = str(meta.get("name") or "")
        email = str(meta.get("user_name") or meta.get("gaia_name") or "")
        found.append((folder, display, email))
    return found


def _print_chrome_profiles() -> None:
    profiles = _list_chrome_profiles()
    print(f"Chrome User Data: {CHROME_USER_DATA}")
    if not profiles:
        print("  (no Default / Profile * folders found)")
        return
    print("Available Chrome profiles on this machine:")
    for folder, display, email in profiles:
        label = display or "(no display name)"
        mail = f" <{email}>" if email else ""
        mark = "  <- CHROME_PROFILE" if folder == CHROME_PROFILE else ""
        print(f"  {folder}: {label}{mail}{mark}")
    print('Set CHROME_PROFILE in worker.py to the Folder name (e.g. "Default" or "Profile 1").')


def _ensure_automation_profile_dir() -> None:
    dst = AUTOMATION_USER_DATA / "Default"
    AUTOMATION_USER_DATA.mkdir(parents=True, exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)
    _clear_profile_locks(AUTOMATION_USER_DATA)
    _clear_profile_locks(dst)


def _sync_source_profile() -> bool:
    """Copy CHROME_PROFILE into chrome_automation_data/Default. Returns True if synced."""
    src = CHROME_USER_DATA / CHROME_PROFILE
    dst = AUTOMATION_USER_DATA / "Default"
    if not src.is_dir():
        print(f"[WARN] Source profile not found: {src}")
        _print_chrome_profiles()
        return False

    AUTOMATION_USER_DATA.mkdir(parents=True, exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)
    print(f"Syncing Chrome profile '{CHROME_PROFILE}' → {dst}")

    # Exclude bulky/cache dirs; keep cookies + local storage (login session).
    cmd = [
        "robocopy",
        str(src),
        str(dst),
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NC",
        "/NS",
        "/NP",
        "/XD",
        "Cache",
        "Code Cache",
        "GPUCache",
        "GrShaderCache",
        "ShaderCache",
        "Service Worker",
        "/XF",
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # robocopy: exit codes 0–7 mean success / partial copy
    if result.returncode >= 8:
        raise RuntimeError(f"Profile sync failed (robocopy exit {result.returncode}): {result.stdout}\n{result.stderr}")

    _clear_profile_locks(AUTOMATION_USER_DATA)
    _clear_profile_locks(dst)
    return True


def start_or_attach_chrome() -> webdriver.Chrome:
    print(f"Configured source profile: {CHROME_PROFILE} under {CHROME_USER_DATA}")
    print("Closing Chrome so the automation profile can be opened...")
    _kill_chrome()

    cookies_path = AUTOMATION_USER_DATA / "Default" / "Network" / "Cookies"
    if RESYNC_FROM_MIKE_EACH_RUN or not cookies_path.exists():
        synced = _sync_source_profile()
        if not synced:
            print("Starting a fresh automation Chrome profile instead.")
            print("Sign into LinkedIn once in the opened window; later runs will reuse it.")
            _ensure_automation_profile_dir()
    else:
        print(f"Reusing automation profile at {AUTOMATION_USER_DATA}")
        print("(Keeps LinkedIn login. Set RESYNC_FROM_MIKE_EACH_RUN = True to re-copy from source profile.)")
        _clear_profile_locks(AUTOMATION_USER_DATA)
        _clear_profile_locks(AUTOMATION_USER_DATA / "Default")

    chrome_exe = _find_chrome_exe()

    # Prefer debug-port attach on the *copied* profile (not live User Data).
    if _port_open(DEBUG_HOST, DEBUG_PORT):
        # Stale listener from a previous run — kill and relaunch cleanly.
        _kill_chrome()
        time.sleep(1)

    cmd = [
        str(chrome_exe),
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--remote-debugging-address={DEBUG_HOST}",
        f"--user-data-dir={AUTOMATION_USER_DATA}",
        "--profile-directory=Default",
        "--disable-notifications",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "about:blank",
    ]
    print(f"Starting automation Chrome: {chrome_exe}")
    print(f"user-data-dir={AUTOMATION_USER_DATA}")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        _wait_for_port(DEBUG_HOST, DEBUG_PORT)
        options = Options()
        options.add_experimental_option("debuggerAddress", f"{DEBUG_HOST}:{DEBUG_PORT}")
        driver_local = webdriver.Chrome(options=options)
        print("Attached to Chrome. Navigating…")
        return driver_local
    except Exception as exc:
        print(f"Debug-port attach failed ({exc}). Falling back to Selenium launch…")
        _kill_chrome()
        _clear_profile_locks(AUTOMATION_USER_DATA)
        _clear_profile_locks(AUTOMATION_USER_DATA / "Default")
        options = Options()
        options.binary_location = str(chrome_exe)
        options.add_argument(f"--user-data-dir={AUTOMATION_USER_DATA}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-notifications")
        options.add_argument("--start-maximized")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        driver_local = webdriver.Chrome(options=options)
        print("Selenium launched automation Chrome. Navigating…")
        return driver_local


driver = None  # set in __main__ after loading UI config


def load_run_config():
    """Load names/message/attachment from run_config.json (written by the UI)."""
    cfg_path = Path(__file__).resolve().parent / "run_config.json"
    names = list(people_names)
    template = MESSAGE_TEMPLATE
    attachment = ATTACHMENT_PATH
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            raw_names = data.get("people_names") or []
            parsed = [n.strip() for n in raw_names if isinstance(n, str) and n.strip()]
            if parsed:
                names = parsed
            if isinstance(data.get("message_template"), str) and data["message_template"].strip():
                template = data["message_template"]
            att = data.get("attachment_path")
            if att:
                attachment = str(att)
            elif att is None or att == "":
                attachment = None
            print(f"Loaded run_config.json ({len(names)} people).")
        except Exception as exc:
            print(f"[WARN] Could not read run_config.json: {exc}")
    return names, template, attachment


# =========================
# Login
# =========================
def _dump_login_debug(reason: str) -> Path:
    shot = Path(__file__).resolve().parent / "login_debug.png"
    try:
        driver.save_screenshot(str(shot))
    except Exception:
        pass
    print(f"[DEBUG] {reason}")
    print(f"[DEBUG] URL: {driver.current_url}")
    print(f"[DEBUG] Title: {driver.title}")
    print(f"[DEBUG] Screenshot: {shot}")
    return shot


def _linkedin_auth_cookies() -> set[str]:
    try:
        return {c.get("name", "") for c in driver.get_cookies()}
    except Exception:
        return set()


def _is_logged_in(timeout: int = 5) -> bool:
    """True if LinkedIn session cookie exists or feed chrome is visible."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = (driver.current_url or "").lower()
        names = _linkedin_auth_cookies()
        if ("li_at" in names or "liap" in names) and "login" not in url and "authwall" not in url:
            return True
        try:
            wait_for_any(driver, SELECTORS["login_ok_any"], EC.presence_of_element_located, 2)
            if "login" not in url:
                return True
        except TimeoutException:
            pass
        interruptible_sleep(0.5)
    return False


def _wait_for_manual_login(timeout: int = 300) -> None:
    print(
        "Log into LinkedIn manually in this Chrome window "
        "(use the same account as Mike). Waiting…"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if should_stop():
            raise StopRequested()
        if _is_logged_in(2):
            print("Logged in to LinkedIn.")
            return
        interruptible_sleep(2)
    _dump_login_debug("Manual login timed out")
    raise TimeoutException("Manual LinkedIn login timed out.")


def login_to_linkedin(email: str, password: str) -> None:
    # Copied Chrome cookies often don't transfer LinkedIn auth on modern Chrome.
    # Keep this automation profile logged in after one manual sign-in.
    print("Opening LinkedIn feed…")
    driver.get("https://www.linkedin.com/feed/")
    interruptible_sleep(4)
    if _is_logged_in(15):
        print("Already logged in — continuing.")
        return

    print("LinkedIn session not active in the automation profile.")
    print("Sign in once in the opened Chrome window; later runs will reuse it.")
    driver.get("https://www.linkedin.com/login")
    interruptible_sleep(2)
    _wait_for_manual_login(300)

# =========================
# New message flow
# =========================
def start_new_chat_and_send_message(person_name: str, message: str, attachment_path: Optional[str]) -> None:
    if should_stop():
        raise StopRequested()
    driver.get("https://www.linkedin.com/messaging/thread/new/")
    print("Opening new message window...")
    interruptible_sleep(SLEEPS["pre_message_page"])

    # Select recipient via Enter (top suggestion)
    try:
        search_bar = wait_for_any(driver, SELECTORS["typeahead_input"], EC.visibility_of_element_located, TIMEOUTS["ui"])
        search_bar.send_keys(Keys.CONTROL, "a")
        search_bar.send_keys(Keys.DELETE)
        search_bar.send_keys(person_name)
        interruptible_sleep(SLEEPS["after_name_type"])
        search_bar.send_keys(Keys.ENTER)
        interruptible_sleep(SLEEPS["after_enter"])
        try:
            wait_for_any(driver, SELECTORS["recipient_chip_any"], EC.presence_of_element_located, 3)
            print(f"Selected {person_name}.")
        except TimeoutException:
            print("[INFO] Couldn’t confirm chip via selector; proceeding anyway.")
    except TimeoutException:
        print(f"Could not select {person_name}. Moving onto the next person.")
        return

    try:
        # Focus the New message editor
        message_box = wait_for_any(driver, SELECTORS["editor"], EC.visibility_of_element_located, TIMEOUTS["ui"])
        scroll_into_view(driver, message_box)
        click_js(driver, message_box)
        interruptible_sleep(SLEEPS["after_click_editor"])

        # Paste message via OS clipboard -> Ctrl+V. If that fails, type like a human.
        if not paste_message_via_clipboard(driver, message_box, message):
            print("[INFO] Clipboard paste not detected; typing message slowly.")
            fallback_type_like_human(driver, message_box, message)

        # Verify there is content before proceeding (turns editor white)
        if editor_text_len(driver, message_box) == 0:
            print("[ERROR] Editor still empty after paste/typing; skipping send.")
            return

        # Attach file (optional)
        if attachment_path:
            try:
                attach_btn = wait_for_any(driver, SELECTORS["attach_button_any"], EC.element_to_be_clickable, TIMEOUTS["short"])
                click_js(driver, attach_btn)
            except TimeoutException:
                pass
            file_input = wait_for_any(driver, SELECTORS["file_input"], EC.presence_of_element_located, TIMEOUTS["short"])
            file_input.send_keys(str(Path(attachment_path)))
            print("File attached.")
            interruptible_sleep(SLEEPS["after_attach"])
        else:
            print("No attachment path provided; sending text only.")

        # Send
        send_button = wait_for_any(driver, SELECTORS["send_button"], EC.element_to_be_clickable, TIMEOUTS["ui"])
        scroll_into_view(driver, send_button)
        click_js(driver, send_button)
        print(f"Message and attachment (if any) sent to {person_name}.")
        interruptible_sleep(SLEEPS["after_send"])

    except (TimeoutException, NoSuchElementException, ElementClickInterceptedException) as e:
        print(f"Failed to send the message to {person_name}: {e}")
        driver.get("https://www.linkedin.com/messaging/thread/new/")
        return

# =========================
# Run
# =========================
if __name__ == "__main__":
    clear_stop_flag()
    stopped = False
    try:
        run_names, run_template, run_attachment = load_run_config()
        driver = start_or_attach_chrome()
        login_to_linkedin(EMAIL, PASSWORD)

        for full_name in run_names:
            if should_stop():
                raise StopRequested()
            first_name = full_name.split()[0]
            msg = run_template.format(name=first_name)
            start_new_chat_and_send_message(full_name, msg, run_attachment)
    except StopRequested:
        print("Stop requested — closing Chrome.")
        stopped = True
    except KeyboardInterrupt:
        print("Interrupted — closing Chrome.")
        stopped = True
    finally:
        shutdown_browser()
        clear_stop_flag()
    if stopped:
        sys.exit(130)