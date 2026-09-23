# -*- coding: utf-8 -*-
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

import pyperclip

import egress
import pace

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

JOB_DIR: Path | None = None
USER_DATA_DIR: Path | None = None
DEBUG_HOST = "127.0.0.1"
DEBUG_PORT = 9222
_chrome_proc: subprocess.Popen | None = None
_forwarder: egress.LocalForwarder | None = None
PROXY_LOCAL_PORT: int | None = None

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
        (By.CSS_SELECTOR, "input[type='password']"),
    ],
    "login_submit": [
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "button[data-id='sign-in-form__submit-btn']"),
        (By.CSS_SELECTOR, "button[data-litms-control-urn='login-submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.CSS_SELECTOR, ".login__form_action_container button"),
        (By.CSS_SELECTOR, "button.from__button--floating"),
        (By.XPATH, "//button[contains(.,'Sign in')]"),
        (By.XPATH, "//button[contains(.,'Sign In')]"),
        (By.XPATH, "//button[normalize-space()='Continue']"),
        (By.XPATH, "//button[normalize-space()='Next']"),
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
    "attachment_preview": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[contains(@class,'msg-form__attachment')]"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[contains(@class,'attachment-preview')]"),
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//*[contains(@class,'msg-form__file')]"),
        (By.CSS_SELECTOR, "form [class*='msg-form__attachment']"),
        (By.CSS_SELECTOR, "form [class*='attachment-preview']"),
    ],
    "send_button": [
        (By.XPATH, "//div[.//h2[contains(.,'New message')]]//form//button[contains(@class,'msg-form__send-button') and not(@disabled)]")
    ],
}

# =========================
# Stop / shutdown
# =========================
class StopRequested(Exception):
    """User clicked Stop in the UI (or Ctrl+C)."""


class PaceStop(Exception):
    """Daily, weekly, or hourly cap — or LinkedIn is showing an account limit."""

    def __init__(self, outcome: str, log_message: str, note: str):
        super().__init__(log_message)
        self.outcome = outcome
        self.note = note


_LINKEDIN_LIMIT_PHRASES = (
    "unusual activity from your account",
    "your account has been restricted",
    "temporarily restricted",
    "reached the weekly invitation limit",
)


def _stop_flag() -> Path:
    if JOB_DIR is not None:
        return JOB_DIR / "stop"
    return Path(__file__).resolve().parent / "sendline.stop"


def clear_stop_flag() -> None:
    try:
        _stop_flag().unlink(missing_ok=True)
    except OSError:
        pass


def should_stop() -> bool:
    return _stop_flag().is_file()


def interruptible_sleep(seconds: float) -> None:
    deadline = time.time() + seconds
    while True:
        if should_stop():
            raise StopRequested()
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def start_residential_proxy(proxy_url: str) -> None:
    global _forwarder, PROXY_LOCAL_PORT
    stop_residential_proxy()
    parsed = egress.parse_proxy_line(proxy_url)
    forwarder = egress.LocalForwarder(parsed)
    forwarder.start()
    _forwarder = forwarder
    PROXY_LOCAL_PORT = forwarder.port
    try:
        public_ip = egress.probe(forwarder.port)
    except Exception:
        stop_residential_proxy()
        raise
    if public_ip:
        print(f"Residential exit {parsed.label} is up (public IP {public_ip}).")
    else:
        print(f"Residential exit {parsed.label} accepted a connection.")


def stop_residential_proxy() -> None:
    global _forwarder, PROXY_LOCAL_PORT
    forwarder = _forwarder
    _forwarder = None
    PROXY_LOCAL_PORT = None
    if forwarder is not None:
        forwarder.stop()


def shutdown_browser() -> None:
    global driver
    print("Closing Chrome...")
    _kill_job_chrome()
    try:
        if driver is not None:
            driver.quit()
    except Exception:
        pass
    driver = None
    _kill_job_chrome()
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

def _js_close_attachment_upload_box(driver) -> str:
    """Click Done/Dismiss on LinkedIn's attach overlay, not the remove-file control."""
    return driver.execute_script(
        """
        const isVisible = (el) => {
          if (!el) return false;
          const s = window.getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
        };
        const textOf = (el) => ((el && (el.innerText || el.textContent)) || '').trim();
        const labelOf = (el) => ((el && (el.getAttribute('aria-label') || el.getAttribute('title'))) || '');
        const headingOf = (root) => textOf(root.querySelector('h1, h2, h3, header, .artdeco-modal__header'));
        const looksLikeAttachUi = (root) => {
          const blob = (headingOf(root) + ' ' + textOf(root) + ' ' + labelOf(root)).toLowerCase();
          return /attach|upload|file|document|photo|media/.test(blob)
            || !!root.querySelector('input[type="file"]');
        };
        const roots = [...document.querySelectorAll('.artdeco-modal, [role="dialog"], [class*="artdeco-modal"]')]
          .filter(isVisible)
          .filter((el) => !/^new message$/i.test(headingOf(el)))
          .filter(looksLikeAttachUi);
        for (const root of roots) {
          const buttons = [...root.querySelectorAll('button')].filter(isVisible);
          const done = buttons.find((b) => /^(done|add|insert)$/i.test(textOf(b)));
          if (done) { done.click(); return 'done'; }
          const dismiss = buttons.find((b) => {
            const a = (labelOf(b) + ' ' + textOf(b)).toLowerCase();
            if (/remove|delete|cancel/.test(a)) return false;
            return /dismiss|close/.test(a)
              || (b.className || '').toString().includes('artdeco-modal__dismiss');
          });
          if (dismiss) { dismiss.click(); return 'dismiss'; }
        }
        return '';
        """
    )


def wait_for_attachment_preview() -> None:
    try:
        wait_for_any(driver, SELECTORS["attachment_preview"], EC.presence_of_element_located, TIMEOUTS["ui"])
        print("Attachment preview is in the message.")
    except TimeoutException:
        print("[INFO] Couldn’t confirm the attachment preview; continuing.")


def close_attachment_upload_box() -> None:
    """Close LinkedIn's upload/attachment box after the file is attached, then refocus the editor."""
    print("Closing LinkedIn attachment box…")
    for _ in range(5):
        if should_stop():
            raise StopRequested()
        clicked = ""
        try:
            clicked = _js_close_attachment_upload_box(driver) or ""
        except Exception:
            clicked = ""
        if not clicked:
            break
        print(f"Dismissed attachment box ({clicked}).")
        interruptible_sleep(0.45)
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except Exception:
        pass
    interruptible_sleep(0.35)
    try:
        editor = wait_for_any(driver, SELECTORS["editor"], EC.presence_of_element_located, TIMEOUTS["short"])
        scroll_into_view(driver, editor)
        click_js(driver, editor)
    except Exception:
        pass
    interruptible_sleep(0.4)
    print("Attachment box closed.")

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
# Browser setup — per-user --user-data-dir. Never taskkill all Chrome.
# =========================
def _automation_user_data() -> Path:
    if USER_DATA_DIR is not None:
        return USER_DATA_DIR
    return Path(__file__).resolve().parent / "data" / "chrome_fallback"


def _pid_file() -> Path | None:
    if JOB_DIR is None:
        return None
    return JOB_DIR / "chrome.pid"


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
        if should_stop():
            raise StopRequested()
        if _port_open(host, port):
            return
        time.sleep(0.4)
    raise TimeoutException(f"Chrome debug port {host}:{port} did not open in time.")


def _pids_listening_on(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in (result.stdout or "").splitlines():
        if "LISTEN" not in line.upper():
            continue
        if needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def _kill_pid_tree(pid: int | None) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
        return
    try:
        os.kill(int(pid), 15)
    except OSError:
        pass


def _read_chrome_pid() -> int | None:
    path = _pid_file()
    if path is None or not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def _write_chrome_pid(pid: int | None) -> None:
    path = _pid_file()
    if path is None or not pid:
        return
    path.write_text(str(int(pid)) + "\n", encoding="utf-8")


def _kill_job_chrome() -> None:
    global _chrome_proc
    pid = _read_chrome_pid()
    if _chrome_proc is not None and _chrome_proc.poll() is None:
        pid = pid or _chrome_proc.pid
    _kill_pid_tree(pid)
    for listener in _pids_listening_on(DEBUG_PORT):
        if listener != pid:
            _kill_pid_tree(listener)
    _chrome_proc = None
    time.sleep(1)


def _clear_profile_locks(folder: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock = folder / name
        try:
            if lock.exists() or lock.is_symlink():
                lock.unlink()
        except OSError:
            pass


def _proxy_chrome_args() -> list[str]:
    if not PROXY_LOCAL_PORT:
        return []
    return [
        f"--proxy-server=http://127.0.0.1:{PROXY_LOCAL_PORT}",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    ]


def _ensure_automation_profile_dir() -> Path:
    root = _automation_user_data()
    dst = root / "Default"
    root.mkdir(parents=True, exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)
    _clear_profile_locks(root)
    _clear_profile_locks(dst)
    return root


def start_or_attach_chrome() -> webdriver.Chrome:
    global _chrome_proc
    profile = _ensure_automation_profile_dir()
    cookies_path = profile / "Default" / "Network" / "Cookies"
    if cookies_path.exists():
        print(f"Reusing automation profile at {profile}")
    else:
        print(f"Starting a fresh automation Chrome profile at {profile}")
        print("Sign into LinkedIn once in the opened window; later runs will reuse it.")

    if _port_open(DEBUG_HOST, DEBUG_PORT):
        print("Closing a leftover automation Chrome on the debug port...")
        _kill_job_chrome()

    chrome_exe = _find_chrome_exe()
    cmd = [
        str(chrome_exe),
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--remote-debugging-address={DEBUG_HOST}",
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--disable-notifications",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        *_proxy_chrome_args(),
        "about:blank",
    ]
    print(f"Starting automation Chrome: {chrome_exe}")
    print(f"user-data-dir={profile}")
    _chrome_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _write_chrome_pid(_chrome_proc.pid)

    try:
        _wait_for_port(DEBUG_HOST, DEBUG_PORT)
        options = Options()
        options.add_experimental_option("debuggerAddress", f"{DEBUG_HOST}:{DEBUG_PORT}")
        driver_local = webdriver.Chrome(options=options)
        print("Attached to Chrome. Navigating…")
        return driver_local
    except StopRequested:
        raise
    except Exception as exc:
        print(f"Debug-port attach failed ({exc}). Falling back to Selenium launch…")
        _kill_job_chrome()
        _ensure_automation_profile_dir()
        options = Options()
        options.binary_location = str(chrome_exe)
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-notifications")
        options.add_argument("--start-maximized")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        for arg in _proxy_chrome_args():
            options.add_argument(arg)
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        driver_local = webdriver.Chrome(options=options)
        service = getattr(driver_local, "service", None)
        proc = getattr(service, "process", None)
        if proc is not None and getattr(proc, "pid", None):
            _write_chrome_pid(proc.pid)
        print("Selenium launched automation Chrome. Navigating…")
        return driver_local


driver = None  # set in __main__ after loading job config


def _delete_secrets_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def load_job_config():
    """Load names/message/attachment/credentials from the job folder. Deletes secrets after read."""
    if JOB_DIR is None:
        raise RuntimeError("Job directory is not set.")
    run_path = JOB_DIR / "run.json"
    secrets_path = JOB_DIR / "secrets.json"
    data = json.loads(run_path.read_text(encoding="utf-8"))
    raw_names = data.get("people_names") or []
    names = [n.strip() for n in raw_names if isinstance(n, str) and n.strip()]
    template = data.get("message_template") if isinstance(data.get("message_template"), str) else ""
    att = data.get("attachment_path")
    attachment = str(att) if att else None
    email = ""
    password = ""
    proxy_url = ""
    try:
        secret = json.loads(secrets_path.read_text(encoding="utf-8")) if secrets_path.is_file() else {}
        user = secret.get("linkedin_username") or secret.get("email")
        if isinstance(user, str) and user.strip():
            email = user.strip()
        pwd = secret.get("linkedin_password") if secret.get("linkedin_password") is not None else secret.get("password")
        if isinstance(pwd, str) and pwd:
            password = pwd
        raw_proxy = secret.get("proxy_url")
        if isinstance(raw_proxy, str) and raw_proxy.strip():
            proxy_url = raw_proxy.strip()
    finally:
        _delete_secrets_file(secrets_path)
    preset = pace.normalize(data.get("pace_preset"))
    ledger = data.get("ledger_path")
    ledger_path = Path(str(ledger)) if isinstance(ledger, str) and ledger.strip() else pace.ledger_path(JOB_DIR)
    print(f"Loaded job ({len(names)} people).")
    return names, template, attachment, email, password, preset, ledger_path, proxy_url


# =========================
# Login
# =========================
def _dump_login_debug(reason: str) -> Path:
    shot = (JOB_DIR or Path(__file__).resolve().parent) / "login_debug.png"
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
        "If LinkedIn shows a check or extra step, complete it in this Chrome window. Waiting…"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if should_stop():
            raise StopRequested()
        if _is_logged_in(2):
            print("Logged in to LinkedIn.")
            _raise_if_linkedin_limited()
            return
        interruptible_sleep(2)
    _dump_login_debug("LinkedIn login timed out")
    raise TimeoutException("LinkedIn login timed out.")


def _fill_input(el, value: str) -> None:
    scroll_into_view(driver, el)
    click_js(driver, el)
    interruptible_sleep(0.15)
    try:
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
    except Exception:
        pass
    try:
        driver.execute_script(
            "arguments[0].value = '';"
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));",
            el,
        )
    except Exception:
        pass
    el.send_keys(value)
    try:
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            el,
        )
    except Exception:
        pass


def _click_login_submit() -> bool:
    try:
        btn = wait_for_any(driver, SELECTORS["login_submit"], EC.element_to_be_clickable, TIMEOUTS["short"])
        click_js(driver, btn)
        return True
    except TimeoutException:
        return False


def linkedin_limit_message() -> str | None:
    """Return a short reason if LinkedIn is showing an account restriction."""
    try:
        text = driver.execute_script(
            "return (document.body && document.body.innerText) ? document.body.innerText.slice(0, 8000) : ''"
        )
    except Exception:
        return None
    if not isinstance(text, str) or not text:
        return None
    lowered = text.lower()
    for phrase in _LINKEDIN_LIMIT_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def _raise_if_linkedin_limited() -> None:
    phrase = linkedin_limit_message()
    if not phrase:
        return
    raise PaceStop(
        "restricted",
        "LinkedIn is showing an account limit (" + phrase + "). Stopped so this account is not pushed further.",
        "LinkedIn showed an account limit, so sending stopped.",
    )


def login_to_linkedin(email: str, password: str) -> None:
    print("Opening LinkedIn feed…")
    driver.get("https://www.linkedin.com/feed/")
    interruptible_sleep(4)
    if _is_logged_in(15):
        print("Already logged in — continuing.")
        _raise_if_linkedin_limited()
        return

    if not email or not password:
        print("[ERROR] LinkedIn username/password missing. Add them in Sendline, then launch again.")
        driver.get("https://www.linkedin.com/login")
        interruptible_sleep(2)
        _wait_for_manual_login(300)
        return

    print("Signing in to LinkedIn…")
    driver.get("https://www.linkedin.com/login")
    interruptible_sleep(2)

    try:
        user_el = wait_for_any(driver, SELECTORS["login_username"], EC.visibility_of_element_located, TIMEOUTS["ui"])
        _fill_input(user_el, email)
        interruptible_sleep(0.4)

        try:
            pwd_el = wait_for_any(driver, SELECTORS["login_password"], EC.visibility_of_element_located, 5)
        except TimeoutException:
            print("Username entered — continuing to the password step…")
            _click_login_submit()
            interruptible_sleep(1.2)
            pwd_el = wait_for_any(driver, SELECTORS["login_password"], EC.visibility_of_element_located, TIMEOUTS["ui"])

        _fill_input(pwd_el, password)
        interruptible_sleep(0.4)
        if not _click_login_submit():
            try:
                pwd_el.send_keys(Keys.ENTER)
            except Exception:
                pass
        print("Credentials submitted. Waiting for LinkedIn…")
        interruptible_sleep(3)
        if _is_logged_in(20):
            print("Logged in to LinkedIn.")
            _raise_if_linkedin_limited()
            return
        print("Login not finished yet — complete any extra LinkedIn check in Chrome if shown.")
        _wait_for_manual_login(180)
    except TimeoutException:
        _dump_login_debug("Could not fill LinkedIn login form")
        print("[WARN] Could not fill the login form. Complete sign-in in Chrome.")
        _wait_for_manual_login(180)

# =========================
# New message flow
# =========================
def start_new_chat_and_send_message(person_name: str, message: str, attachment_path: Optional[str]) -> str:
    if should_stop():
        raise StopRequested()
    driver.get("https://www.linkedin.com/messaging/thread/new/")
    print("Opening new message window...")
    interruptible_sleep(SLEEPS["pre_message_page"])
    _raise_if_linkedin_limited()

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
        return "skipped"

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
            return "skipped"

        # Attach file (optional), then close LinkedIn's upload box before Send.
        if attachment_path:
            attach_file = str(Path(attachment_path))
            if not Path(attach_file).is_file():
                print(f"[ERROR] Attachment file not found: {attach_file}")
                return "skipped"
            file_input = None
            try:
                file_input = wait_for_any(driver, SELECTORS["file_input"], EC.presence_of_element_located, 3)
            except TimeoutException:
                try:
                    attach_btn = wait_for_any(
                        driver, SELECTORS["attach_button_any"], EC.element_to_be_clickable, TIMEOUTS["short"]
                    )
                    click_js(driver, attach_btn)
                    interruptible_sleep(0.4)
                except TimeoutException:
                    print("[WARN] Could not find the Attach button.")
                file_input = wait_for_any(
                    driver, SELECTORS["file_input"], EC.presence_of_element_located, TIMEOUTS["short"]
                )
            file_input.send_keys(attach_file)
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    file_input,
                )
            except Exception:
                pass
            print("File attached.")
            wait_for_attachment_preview()
            interruptible_sleep(SLEEPS["after_attach"])
            close_attachment_upload_box()
        else:
            print("No attachment path provided; sending text only.")

        # Send
        send_button = wait_for_any(driver, SELECTORS["send_button"], EC.element_to_be_clickable, TIMEOUTS["ui"])
        scroll_into_view(driver, send_button)
        click_js(driver, send_button)
        print(f"Message and attachment (if any) sent to {person_name}.")
        interruptible_sleep(SLEEPS["after_send"])
        return "sent"

    except PaceStop:
        raise
    except (TimeoutException, NoSuchElementException, ElementClickInterceptedException) as e:
        print(f"Failed to send the message to {person_name}: {e}")
        driver.get("https://www.linkedin.com/messaging/thread/new/")
        return "skipped"

# =========================
# Run
# =========================
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sendline LinkedIn worker")
    parser.add_argument("--job-dir", required=True, help="Job folder with run.json and secrets.json")
    parser.add_argument("--user-data-dir", required=True, help="Per-user Chrome user-data-dir")
    parser.add_argument("--debug-port", type=int, default=9222)
    return parser.parse_args(argv)


def configure(*, job_dir: Path, user_data_dir: Path, debug_port: int) -> None:
    global JOB_DIR, USER_DATA_DIR, DEBUG_PORT
    JOB_DIR = Path(job_dir).resolve()
    USER_DATA_DIR = Path(user_data_dir).resolve()
    DEBUG_PORT = int(debug_port)
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _write_pace_result(outcome: str, note: str) -> None:
    if JOB_DIR is None:
        return
    path = JOB_DIR / "pace_result.json"
    payload = {"outcome": outcome, "note": note}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] Could not save pace result: {exc}")


def _pace_stop(decision: pace.Decision, left: int) -> None:
    message = decision.log_message
    if left > 0:
        message = message.rstrip(".") + f". {left} people are still on the list."
    raise PaceStop(decision.outcome, message, decision.note)


def _wait_until_send_allowed(sends: list, limits: pace.Limits, left: int, *, check_page: bool) -> list:
    """Sleep through a short hourly wait. Stop the run if a longer cap is full."""
    while True:
        if should_stop():
            raise StopRequested()
        if check_page:
            _raise_if_linkedin_limited()
        decision = pace.decide(sends, time.time(), limits)
        if decision.action == "send":
            return sends
        if decision.action == "wait":
            print(decision.log_message)
            interruptible_sleep(decision.seconds)
            continue
        _pace_stop(decision, left)


if __name__ == "__main__":
    args = parse_args()
    configure(job_dir=Path(args.job_dir), user_data_dir=Path(args.user_data_dir), debug_port=int(args.debug_port))

    clear_stop_flag()
    stopped = False
    pace_code = 0
    browser_started = False
    try:
        (
            run_names,
            run_template,
            run_attachment,
            run_email,
            run_password,
            run_preset,
            run_ledger,
            run_proxy,
        ) = load_job_config()
        limits = pace.limits_for(run_preset)
        sends = pace.load_sends(run_ledger)

        pending: list[str] = []
        now = time.time()
        for full_name in run_names:
            previous = pace.recent_send_at(sends, full_name, now)
            if previous is not None:
                print(f"Skipping {full_name} — already messaged on {pace.format_local(previous)}.")
                continue
            pending.append(full_name)

        print(
            f"Pace: {limits.label}. Up to {limits.daily}/day and {limits.weekly}/week, "
            f"with {limits.min_gap // 60}–{limits.max_gap // 60} minutes between messages."
        )
        if not pending:
            print("Everyone on this list was already messaged in the last 90 days.")
        else:
            print(f"{len(pending)} people left after skipping recent messages.")
            sends = _wait_until_send_allowed(sends, limits, len(pending), check_page=False)
            if run_proxy:
                start_residential_proxy(run_proxy)
            browser_started = True
            driver = start_or_attach_chrome()
            login_to_linkedin(run_email, run_password)

        for index, full_name in enumerate(pending):
            if should_stop():
                raise StopRequested()
            sends = _wait_until_send_allowed(sends, limits, len(pending) - index, check_page=True)
            first_name = full_name.split()[0]
            msg = run_template.format(name=first_name)
            outcome = start_new_chat_and_send_message(full_name, msg, run_attachment)
            if outcome != "sent":
                interruptible_sleep(8)
                continue
            sends = pace.append_send(run_ledger, full_name)
            if index + 1 >= len(pending):
                break
            gap = pace.wait_after_send(sends, time.time(), limits)
            if gap.action == "stop":
                _pace_stop(gap, len(pending) - index - 1)
            print(gap.log_message)
            interruptible_sleep(gap.seconds)
    except egress.EgressError as exc:
        print(str(exc))
        pace_code = 1
    except PaceStop as exc:
        print(str(exc))
        _write_pace_result(exc.outcome, exc.note)
        pace_code = 75
    except StopRequested:
        print("Stop requested — closing Chrome.")
        stopped = True
    except KeyboardInterrupt:
        print("Interrupted — closing Chrome.")
        stopped = True
    finally:
        if browser_started:
            shutdown_browser()
        stop_residential_proxy()
        secrets_left = JOB_DIR / "secrets.json"
        try:
            secrets_left.unlink(missing_ok=True)
        except OSError:
            pass
        clear_stop_flag()
    if pace_code:
        sys.exit(pace_code)
    if stopped:
        sys.exit(130)