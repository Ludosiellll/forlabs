"""Проверка кода программы лабы на опасное, прежде чем ForLabs запустит её сам: удаление и запись файлов вне папки
программы, запуск других программ, реестр, сеть, прямые вызовы системы, выполнение кода из строки, опасный SQL.

Нашлось — проект всё равно пишется, но сам ForLabs его не запускает (рисунки делает студент, причина названа).
Ложная тревога так же плоха, как пропуск, поэтому комментарии выбрасываются, а строки проверяются отдельно от кода:
`print("os.system")` не запуск программы, `open("results.txt", "w")` — обычная запись рядом с программой."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from forlabs.runner.plan import ProjectPlan

PROC = "запускает другие программы или команды"
OUT = "обращается к файлам вне папки программы"
REG = "работает с реестром Windows"
NET = "обращается к сети"
NATIVE = "вызывает системные функции напрямую"
CODE = "выполняет произвольный код"
UNSAFE = "небезопасный код"
SQL_DROP = "удаляет чужую базу данных"
SQL_USERS = "меняет пользователей или права MySQL"
SQL_SYSTEM = "обращается к системной базе mysql"
SQL_FILES = "читает или пишет файлы на сервере MySQL"
SQL_SERVER = "меняет настройки сервера MySQL"

LEXER = {".cs": "cs", ".cpp": "c", ".cc": "c", ".cxx": "c", ".c": "c", ".h": "c", ".hpp": "c", ".hxx": "c",
         ".inl": "c", ".py": "py", ".pyw": "py", ".java": "java", ".js": "js", ".mjs": "js", ".cjs": "js",
         ".html": "html", ".htm": "html", ".php": "php", ".go": "go", ".sql": "sql"}
LOCAL_SERVERS = {"javascript", "web", "php"}  # тут сервер на localhost — обычная лаба


def _imp(mods: str) -> str:
    """Импорт модуля Python: `import a, b` или `from a.b import c`."""
    return rf"^[ \t]*(?:from[ \t]+(?:{mods})\b|import[ \t]+[^\n]*\b(?:{mods})\b)"


# (языки, шаблон по коду без комментариев и строк, что делает). Строки в коде заменены на "N" — номер строки.
RULES: list[tuple[str, str, str]] = [
    # «new Process(…)» не ищем: в лабах по планированию процессов это свой класс
    ("cs", r"\bProcess\s*\.\s*Start\b|\bProcessStartInfo\b|\.\s*StartInfo\b|\bInteraction\s*\.\s*Shell\b", PROC),
    ("cs c", r"\b(?:ShellExecute\w*|WinExec|CreateProcess\w*)\b", PROC),
    ("c", r"\b_?w?popen\s*\(|\b_?exec[lv]p?e?\s*\(|\b_?spawn[lv]p?e?\s*\(|\bfork\s*\(", PROC),
    ("py", r"\bos\s*\.\s*(?:system|popen|exec\w*|spawn\w*|startfile|fork\w*|posix_spawn\w*)\b", PROC),
    ("py", _imp("subprocess|pty|webbrowser"), PROC),
    ("py", r"^[ \t]*from[ \t]+os[ \t]+import[^\n]*\b(?:system|popen|startfile|exec\w*|spawn\w*)\b", PROC),
    ("java", r"\bgetRuntime\s*\(\s*\)\s*\.\s*exec\b|\bProcessBuilder\b|\bDesktop\s*\.\s*getDesktop\b", PROC),
    ("go", r"\bexec\s*\.\s*Command(?:Context)?\b|\bos\s*\.\s*StartProcess\b|\bsyscall\s*\.\s*(?:Exec|ForkExec)\b",
     PROC),
    ("php", r"(?<![\w>:$])(?:shell_exec|exec|system|passthru|proc_open|popen|pcntl_exec|pcntl_fork)\s*\(", PROC),

    ("cs", r"\bRegistry(?:Key|Hive|View)?\b", REG),
    ("c", r"\bReg(?:Open|Create|Set|Delete|Query|Get|Enum|Save|Load|Replace|Restore|Copy|Rename|Connect)\w*\s*\(|"
          r"\bHKEY_\w+|\bSH(?:Delete|Set|Get)(?:Key|Value)\w*", REG),
    ("py", _imp("_?winreg"), REG),
    ("java", r"\bPreferences\s*\.\s*(?:user|system)(?:Root|NodeForPackage)\b", REG),

    ("cs", r"\b(?:DllImport|LibraryImport|NativeLibrary)\b", NATIVE),
    ("cs c", r"\b(?:LoadLibrary\w*|GetProcAddress)\b", NATIVE),
    ("c", r"\b(?:VirtualAlloc\w*|VirtualProtect\w*|WriteProcessMemory|ReadProcessMemory|CreateRemoteThread|"
          r"OpenProcess|SetWindowsHookEx\w*|TerminateProcess)\b", NATIVE),
    ("py", _imp(r"ctypes|cffi|_winapi|win32\w*|pywintypes|pythoncom"), NATIVE),
    ("java", r"\bSystem\s*\.\s*(?:load|loadLibrary)\s*\(|\bnative\b|\bsun\s*\.\s*misc\s*\.\s*Unsafe\b", NATIVE),
    ("js", r"\bprocess\s*\.\s*(?:binding|dlopen)\b", NATIVE),
    ("php", r"\bnew\s+COM\s*\(|\bFFI\s*::|\bw32api_\w+", NATIVE),

    ("cs", r"\bAssembly\s*\.\s*(?:Load\w*|UnsafeLoadFrom)\b|\bExecuteAssembly\b|\bCSharpCodeProvider\b|"
           r"\bCompileAssemblyFrom\w+|\bCSharpScript\b", CODE),
    ("py", r"(?<![\w.])(?:eval|exec)\s*\(|\b__import__\b|\bimportlib\b|\b__builtins__\b|"
           r"\bimport\s+builtins\b|\bfrom\s+builtins\b|\b(?:globals|vars|locals)\s*\(\s*\)\s*\[|"
           r"\b__(?:subclasses|globals|code|loader|spec)__\b|\bgetattr\s*\(\s*(?:__builtins__|builtins|sys|os)\b", CODE),
    ("java", r"\bURLClassLoader\b|\bdefineClass\b|\bScriptEngine(?:Manager)?\b|\bgetSystemJavaCompiler\b", CODE),
    ("js", r"(?<![\w.$])eval\s*\(|\bnew\s+Function\s*\(", CODE),
    ("php", r"(?<![\w>:$])(?:eval|create_function)\s*\(", CODE),

    ("cs", r"\bunsafe\b", UNSAFE),

    ("cs", r"\b(?:SpecialFolder|GetFolderPath)\b", OUT),
    ("py", r"\b(?:expanduser|expandvars)\b|\bPath\s*\.\s*home\s*\(", OUT),
    ("java", r"\bget(?:Home|Default)Directory\b", OUT),
    ("go", r"\bos\s*\.\s*User(?:Home|Config|Cache)Dir\b", OUT),
    ("js", r"\bos\s*\.\s*homedir\s*\(|\bprocess\s*\.\s*env\s*\.\s*(?:HOME|USERPROFILE|APPDATA|LOCALAPPDATA|HOMEPATH)\b",
     OUT),
]

# Сетевые вызовы: опасны, если адрес внешний (или неизвестен — для языков, где сервер на localhost не нужен)
NET_API: dict[str, str] = {
    "cs": r"\b(?:HttpClient|WebClient|WebRequest|HttpWebRequest|FtpWebRequest|TcpClient|TcpListener|UdpClient|"
          r"SmtpClient|Socket)\b|\bDns\s*\.|\bnew\s+Ping\s*\(",
    "c": r"#\s*include\s*<(?:winsock2?|ws2tcpip|wininet|winhttp|urlmon|sys/socket|netinet/\w+|arpa/inet|curl/curl)\.h>|"
         r"\bWSAStartup\b|\bURLDownloadToFile\w*|\bInternet(?:Open|Connect|ReadFile)\w*|\bWinHttp\w+|\bcurl_easy_\w+|"
         r"\bsocket\s*\(\s*[AP]F_",
    "py": _imp(r"requests|urllib\d?|http|httpx|aiohttp|socket|socketserver|ftplib|smtplib|poplib|imaplib|telnetlib|"
               r"xmlrpc|websockets?|paramiko"),
    "java": r"\b(?:Socket|ServerSocket|DatagramSocket|HttpURLConnection|URLConnection|HttpClient|HttpRequest)\b|"
            r"\bnew\s+(?:java\s*\.\s*net\s*\.\s*)?URL\s*\(|\bopen(?:Stream|Connection)\s*\(",
    "go": r"\bhttp\s*\.\s*(?:Get|Post|PostForm|Head|NewRequest\w*|DefaultClient|Client|ListenAndServe\w*)\b|"
          r"\bnet\s*\.\s*(?:Dial\w*|Listen\w*)\b|\bsmtp\s*\.\s*SendMail\b",
    "js": r"\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\b|\bEventSource\b|\baxios\b|\bsendBeacon\b|"
          r"\$\s*\.\s*(?:ajax|get|post|getJSON)\s*\(|\bhttps?\s*\.\s*(?:get|request)\s*\(|"
          r"\bnet\s*\.\s*(?:connect|createConnection)\s*\(",
    "php": r"\b(?:curl_\w+|fsockopen|pfsockopen|stream_socket_client|socket_create|socket_connect|ftp_connect|"
           r"get_headers|file_get_contents|fopen|readfile|simplexml_load_file)\s*\(|\bSoapClient\b",
}

# Модули по имени в строке: require("…")/import "…" (JS), import "…" (Go)
MODULES: dict[str, dict[str, str]] = {
    "js": {"child_process": PROC, "shelljs": PROC, "execa": PROC, "cross-spawn": PROC, "winreg": REG,
           "regedit": REG, "ffi-napi": NATIVE, "ffi": NATIVE, "koffi": NATIVE, "vm": CODE},
    "go": {"os/exec": PROC, "golang.org/x/sys/windows/registry": REG, "syscall": NATIVE, "C": NATIVE,
           "plugin": NATIVE, "golang.org/x/sys/windows": NATIVE, "unsafe": UNSAFE},
}
JS_NET_MODULES = {"net", "dgram", "tls", "https", "axios", "node-fetch", "got", "ws", "request"}

SYSTEM_SHELL = re.compile(r"\s*(?:pause|cls|clear|chcp\s+\d+|color\s+[0-9a-f]{1,2}|title\s[^&|<>^%]*?|"
                          r"mode\s+con[^&|<>^%]*?)(?:\s*>\s*nul)?\s*", re.I)  # system("pause") — обычное в лабах C++
ENV_ANY_CASE = {"userprofile", "appdata", "localappdata", "homepath", "homedrive", "systemroot", "windir",
                "programfiles", "programdata", "onedrive", "systemdrive"}
ENV_EXACT = {"HOME", "TEMP", "TMP", "PUBLIC", "user.home"}
ENV_PERCENT = re.compile(r"%(?:USERPROFILE|APPDATA|LOCALAPPDATA|HOMEPATH|HOMEDRIVE|TEMP|TMP|SYSTEMROOT|WINDIR|"
                         r"PROGRAMFILES(?:\(X86\))?|PROGRAMDATA|PUBLIC|ONEDRIVE|SYSTEMDRIVE)%|\$env:|\$HOME\b", re.I)
WIN_ABS = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|^\\\\\w")
UNIX_ABS = re.compile(r"^/(?:etc|root|usr|var|bin|sbin|boot|dev|proc|sys|tmp|opt|mnt|home|Users|Library)(?:/|$)")
UNIX_ABS_WEB = re.compile(r"^/(?:etc|root|usr|var|bin|sbin|boot|dev|proc|sys|tmp)/")  # «/home» в вебе — маршрут
URL = re.compile(r"\s*(?:https?|wss?|ftp)://(\[[^\]]*\]|[^/:?#\s]+)", re.I)
IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

SQL_START = re.compile(r"\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|GRANT|REVOKE|USE|LOAD|SET|SHOW|RENAME|"
                       r"TRUNCATE|CALL|REPLACE|SHUTDOWN|INSTALL|FLUSH)\b", re.I)
SQL_CREATE_DB = re.compile(r"\bCREATE\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)", re.I)
SQL_DROP_DB = re.compile(r"\bDROP\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+EXISTS\s+)?`?(\w+)", re.I)
SQL_SYSTEM_DBS = {"mysql", "sys", "information_schema", "performance_schema"}
# Шаблоны по форме запроса, а не по слову: строки интерфейса вроде «Create user account» или «Set global options»
# начинаются как SQL, но запросом не являются
SQL_RULES = [
    (r"\b(?:CREATE|DROP|ALTER|RENAME)\s+USER\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:'[^']*'|\"[^\"]*\"|`[^`]*`|\w+)\s*"
     r"(?:@|IDENTIFIED\b|;|,)|\bGRANT\s[^;]*?\bON\s[^;]*?\bTO\b|\bREVOKE\s[^;]*?\bON\s[^;]*?\bFROM\b|"
     r"\bSET\s+PASSWORD\s*(?:FOR\s+\S+\s*)?=", SQL_USERS),
    (r"`?\bmysql`?\s*\.\s*`?\w+|\bUSE\s+`?mysql`?\s*(?:;|$)", SQL_SYSTEM),
    (r"\bLOAD\s+DATA\s+(?:LOW_PRIORITY\s+|CONCURRENT\s+)?(?:LOCAL\s+)?INFILE\b|\bLOAD_FILE\s*\(|"
     r"\bINTO\s+(?:OUT|DUMP)FILE\b", SQL_FILES),
    (r"\bSET\s+(?:GLOBAL|PERSIST(?:_ONLY)?)\s+\w+\s*:?=|\bSET\s+@@(?:global|persist)|^\s*SHUTDOWN\s*(?:;|$)|"
     r"\bINSTALL\s+(?:PLUGIN|COMPONENT)\b|\bSONAME\b", SQL_SERVER),
]

TOKENS = {
    "c": r'//|/\*|"|\'', "cs": r'//|/\*|"""|"|\'', "java": r'//|/\*|"""|"|\'', "js": r'//|/\*|"|\'|`',
    "go": r'//|/\*|"|\'|`', "py": r'#|"""|\'\'\'|"|\'', "php": r'//|/\*|#|<<<|"|\'|`', "sql": r'--|#|/\*|"|\'|`',
}
ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
HEREDOC = re.compile(r"<<<[ \t]*(['\"]?)(\w+)\1[ \t]*\r?\n")
CPP_RAW = re.compile(r'"([^()\\\s]{0,16})\(')


@dataclass
class _Src:
    path: str
    lang: str        # набор правил: cs, c, py, java, js, php, go, sql
    code: str        # код без комментариев; каждая строка-литерал заменена на "N"
    strings: list    # (номер строки файла, содержимое после разбора экранирования, вид: "" или "shell")
    depth: int       # глубина папки файла внутри проекта (для «..»)
    browser: bool    # код работает в браузере (страница сайта): файлы и пути браузер не даёт трогать

    def line(self, pos: int) -> int:
        if not hasattr(self, "_nl"):
            self._nl = [i for i, c in enumerate(self.code) if c == "\n"]
        return bisect.bisect_left(self._nl, pos) + 1

    def show(self, s: str) -> str:
        """Совпадение для человека: вместо "N" — сама строка."""
        s = re.sub(r'"(\d+)"', lambda m: '"' + _short(self.strings[int(m[1])][1], 30) + '"', s)
        return _short(" ".join(s.split()).rstrip("( "), 60)


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _unescape(s: str) -> str:
    return re.sub(r"\\([\\'\"`ntr0abfv])", lambda m: ESC.get(m[1], m[1]), s)


def _prefix(text: str, s: int) -> str:
    """Буквы прямо перед кавычкой (r"…" в Python, R"(…)" в C++), если это не конец обычного слова."""
    k = s
    while k > 0 and s - k < 3 and text[k - 1].isalpha():
        k -= 1
    if k > 0 and (text[k - 1].isalnum() or text[k - 1] == "_"):
        return ""
    return text[k:s]


def _quoted_end(text: str, i: int, q: str, escapes: bool, multiline: bool, doubled: bool = False) -> tuple[int, int]:
    """Конец строки в кавычках q с позиции i (после открывающей): (конец содержимого, позиция после закрывающей)."""
    n = len(text)
    while i < n:
        c = text[i]
        if escapes and c == "\\":
            i += 2
            continue
        if doubled and text.startswith(q + q, i):  # "" внутри @"…" в C#
            i += 2
            continue
        if text.startswith(q, i):
            return i, i + len(q)
        if c == "\n" and not multiline:
            return i, i
        i += 1
    return n, n


def lex(text: str, lang: str) -> tuple[str, list]:
    """Код без комментариев (переводы строк сохранены — номера строк те же) и список строк-литералов."""
    tok = re.compile(TOKENS[lang])
    code: list[str] = []
    strings: list[tuple[int, str, str]] = []
    line = 1
    i = 0

    def put(s: str) -> None:
        nonlocal line
        code.append(s)
        line += s.count("\n")

    def put_str(start: int, end: int, content: str, kind: str = "") -> None:
        strings.append((line, content, kind))
        put(f'"{len(strings) - 1}"' + "\n" * text.count("\n", start, end))

    while (m := tok.search(text, i)) is not None:
        s, t = m.start(), m.group()
        put(text[i:s])
        i = m.end()
        if t in ("//", "#", "--"):
            if t == "--" and text[s + 2:s + 3] not in ("", " ", "\t", "\r", "\n"):
                put(t)
                continue
            e = text.find("\n", s)
            i = len(text) if e < 0 else e
        elif t == "/*":
            e = text.find("*/", s + 2)
            i = len(text) if e < 0 else e + 2
            if lang == "sql" and text.startswith("/*!", s):  # «исполняемый комментарий» MySQL — это код
                body = text[s + 3:(e if e >= 0 else len(text))]
                put(" " + re.sub(r"^\d{5,6}", "", body) + " ")
            else:
                put("\n" * text.count("\n", s, i))
        elif t == "<<<":
            h = HEREDOC.match(text, s)
            if not h:
                put(t)
                continue
            end = re.compile(r"^[ \t]*" + h[2] + r"\b", re.M).search(text, h.end())
            body_end, i = (end.start(), end.end()) if end else (len(text), len(text))
            body = text[h.end():body_end]
            put_str(s, i, body if h[1] == "'" else _unescape(body))
        elif t in ('"""', "'''"):
            raw = lang == "cs" or (lang == "py" and "r" in _prefix(text, s).lower())
            e, i = _quoted_end(text, i, t, not raw, True)
            body = text[s + 3:e]
            put_str(s, i, body if raw else _unescape(body))
        elif t == "`":
            if lang == "sql":  # `имя` в MySQL — просто имя
                e = text.find("`", i)
                e = len(text) if e < 0 else e
                put(text[i:e])
                i = e + 1
                continue
            raw = lang == "go"
            e, i = _quoted_end(text, i, t, not raw, True)
            body = text[s + 1:e]
            put_str(s, i, body if raw else _unescape(body), "shell" if lang == "php" else "")
        elif t == "'" and lang == "c" and s > 0 and text[s - 1].isalnum():
            put(t)  # 1'000'000 — разделитель разрядов C++14
        elif lang == "cs" and t == '"' and (text[s - 1:s] == "@" or text[s - 2:s] in ("@$", "$@")):
            e, i = _quoted_end(text, i, t, False, True, doubled=True)
            put_str(s, i, text[s + 1:e].replace('""', '"'))
        elif lang == "c" and t == '"' and _prefix(text, s) in ("R", "LR", "uR", "UR") and (r := CPP_RAW.match(text, s)):
            close = ")" + r[1] + '"'
            e = text.find(close, r.end())
            e = len(text) if e < 0 else e
            i = min(len(text), e + len(close))
            put_str(s, i, text[r.end():e])
        else:
            raw = lang == "py" and "r" in _prefix(text, s).lower()
            e, i = _quoted_end(text, i, t, not raw, lang in ("php", "sql"))
            body = text[s + 1:e]
            put_str(s, i, body if raw else _unescape(body))
    put(text[i:])
    return "".join(code), strings


def _blank(s: str) -> str:
    return "\n" * s.count("\n")


def _html_scripts(text: str) -> str:
    """Только JavaScript страницы (содержимое <script>), остальное — пустые строки (номера строк те же)."""
    text = re.sub(r"<!--.*?-->", lambda m: _blank(m[0]), text, flags=re.S)
    out, pos = [], 0
    for m in re.finditer(r"(<script\b[^>]*>)(.*?)</script\s*>", text, re.S | re.I):
        out.append(_blank(text[pos:m.start(2)]))
        out.append(m[2])
        pos = m.end(2)
    out.append(_blank(text[pos:]))
    return "".join(out)


def _php_split(text: str) -> tuple[str, str]:
    """Файл PHP: (код PHP, разметка HTML), каждое с пустыми строками на месте другого."""
    php, html, pos = [], [], 0
    for m in re.finditer(r"<\?(?:php\b|=)?(.*?)(?:\?>|\Z)", text, re.S | re.I):
        html.append(text[pos:m.start()])
        php.append(_blank(text[pos:m.start(1)]))
        php.append(m[1])
        php.append(_blank(text[m.end(1):m.end()]))
        html.append(_blank(m[0]))
        pos = m.end()
    html.append(text[pos:])
    php.append(_blank(text[pos:]))
    return "".join(php), "".join(html)


def _sources(plan: ProjectPlan) -> list[_Src]:
    out = []
    for f in plan.files:
        kind = LEXER.get(PurePosixPath(f.path).suffix.lower())
        if kind is None:
            continue
        depth = len(PurePosixPath(f.path).parts) - 1
        parts: list[tuple[str, str, bool]] = []
        if kind == "html":
            parts.append(("js", _html_scripts(f.content), True))
        elif kind == "php":
            php, html = _php_split(f.content)
            parts += [("php", php, False), ("js", _html_scripts(html), True)]
        else:
            parts.append((kind, f.content, kind == "js" and plan.language in ("web", "php")))
        for lang, text, browser in parts:
            code, strings = lex(text, lang)
            out.append(_Src(f.path, lang, code, strings, depth, browser))
    return out


def _escapes(path: str, depth: int) -> bool:
    """Путь с «..» уходит выше папки проекта (считая от папки файла)."""
    segs = path.strip().replace("\\", "/").split("/")
    if ".." not in segs:
        return False
    for s in segs:
        if s == "..":
            depth -= 1
            if depth < 0:
                return True
        elif s not in ("", "."):
            depth += 1
    return False


def _path_problem(s: str, src: _Src) -> str | None:
    if WIN_ABS.search(s) or (UNIX_ABS_WEB if src.lang in ("js", "php") else UNIX_ABS).match(s):
        return _short(s, 40)
    if s.startswith(("~/", "~\\")) or ENV_PERCENT.search(s):
        return _short(s, 40)
    if s.strip().lower() in ENV_ANY_CASE or s.strip() in ENV_EXACT:
        return s.strip()
    if _escapes(s, src.depth):
        return _short(s, 40)
    return None


def _host_kind(s: str) -> str | None:
    """"local", "external" или None (строка не адрес)."""
    m = URL.match(s)
    host = (m[1] if m else s.strip()).strip("[]").lower()
    if not m and not (IPV4.fullmatch(host) or host == "localhost"):
        return None
    if host in ("localhost", "::1") or host.startswith("127.") or host.endswith(".localhost"):
        return "local"
    return "external"


def _modules(src: _Src) -> list[tuple[int, str]]:
    """Импортируемые модули, заданные строкой (JS, Go): (строка, имя)."""
    if src.lang == "js":
        pat = r'\brequire\s*\(\s*"(\d+)"\s*\)|\bimport\s*\(\s*"(\d+)"|\bfrom\s*"(\d+)"|\bimport\s*"(\d+)"'
        idx = [next(g for g in m.groups() if g) for m in re.finditer(pat, src.code)]
    elif src.lang == "go":
        idx = [g for m in re.finditer(r"\bimport\s*\(([^)]*)\)", src.code) for g in re.findall(r'"(\d+)"', m[1])]
        idx += re.findall(r'\bimport\s+(?:[\w.]+\s+)?"(\d+)"', src.code)
    else:
        return []
    out = []
    for i in idx:
        line, name, _ = src.strings[int(i)]
        out.append((line, name.removeprefix("node:")))
    return out


# что можно на своём одноразовом сервере ForLabs (лаба на MySQL), но не на сервере студента (SQL из программы)
OWN_SERVER_OK = {SQL_USERS, SQL_SYSTEM}


def _sql(text: str, created: set[str], src: _Src | None = None, own: bool = False) -> list[tuple[int, str, str]]:
    """Опасный SQL в тексте (файл .sql — src задан, иначе строка из кода): (строка, что делает, фрагмент).
    Свою базу (её CREATE DATABASE есть в проекте) удалять можно. own — запросы идут на свой чистый сервер ForLabs:
    пользователи, права и чужие (там их нет) базы — тоже можно."""
    out = []
    for m in SQL_DROP_DB.finditer(text):
        name = m[1].lower()
        if name in SQL_SYSTEM_DBS or (name not in created and not own):
            out.append((src.line(m.start()) if src else 0, SQL_DROP, " ".join(m[0].split()).replace("`", "")))
    for pat, what in SQL_RULES:
        if own and what in OWN_SERVER_OK:
            continue
        for m in re.finditer(pat, text, re.I | re.M):
            out.append((src.line(m.start()) if src else 0, what, src.show(m[0]) if src else _short(m[0], 40)))
    return out


# команды самого клиента mysql: пишутся без «;» и заканчиваются вместе со строкой
CLIENT_CMD = re.compile(r"(?:source|use|tee|notee|status|charset|connect|delimiter|warnings|nowarning|prompt|"
                        r"rehash|help|exit|quit|ego|go|print|pager|nopager|system|edit|resetconnection)\b|\\.|\?",
                        re.I)


def _mysql_client(src: _Src) -> list[tuple[int, str, str]]:
    """Команды клиента mysql, запускающие программы: «system …» в начале запроса и «\\!» в любом месте строки."""
    out, start = [], True
    for n, raw in enumerate(src.code.split("\n"), 1):
        s = raw.strip()
        if (start and re.match(r"system\s", s, re.I)) or "\\!" in s:
            out.append((n, PROC, _short(s, 40)))
        if s:
            start = s.endswith((";", "//", "$$", "\\g", "\\G")) or bool(start and CLIENT_CMD.match(s))
    return out


def _scan_source(src: _Src, plan: ProjectPlan, created: set[str]) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for langs, pat, what in RULES:
        if src.lang in langs.split():
            for m in re.finditer(pat, src.code, re.M):
                found.append((src.line(m.start()), what, src.show(m[0])))
    if src.lang == "c":
        for m in re.finditer(r'\bsystem\s*\(\s*(?:"(\d+)"\s*\))?', src.code):
            if m[1] is None or not SYSTEM_SHELL.fullmatch(src.strings[int(m[1])][1]):
                found.append((src.line(m.start()), PROC, src.show(m[0])))
    for line, name in _modules(src):
        what = MODULES[src.lang].get(name)
        if what is None and src.lang == "go" and name.startswith("golang.org/x/sys/windows"):
            what = NATIVE
        if what:
            found.append((line, what, name))
    for line, s, kind in src.strings:
        if kind == "shell":
            found.append((line, PROC, f"`{_short(s, 30)}`"))
        if not src.browser and src.lang != "sql" and (p := _path_problem(s, src)):
            found.append((line, OUT, p))
        if src.lang != "sql" and SQL_START.match(s):
            found += [(line, w, t) for _, w, t in _sql(s, created)]
    if src.lang == "sql":  # .sql лабы на MySQL выполняет только свой сервер; в PHP-проекте — сервер студента
        found += _sql(src.code, created, src, own=plan.language == "mysql") + _mysql_client(src)
    found += _network(src, plan)
    return found


def _network(src: _Src, plan: ProjectPlan) -> list[tuple[int, str, str]]:
    api = None
    if (pat := NET_API.get(src.lang)) and (m := re.search(pat, src.code, re.M)):
        api = (src.line(m.start()), src.show(m[0]))
    if src.lang == "js" and api is None:
        api = next(((line, name) for line, name in _modules(src) if name in JS_NET_MODULES), None)
    if api is None:
        return []
    hosts = [(line, s, k) for line, s, _ in src.strings if (k := _host_kind(s))]
    external = [(line, s) for line, s, k in hosts if k == "external"]
    if external:
        return [(api[0], NET, _short(external[0][1], 40))]
    if plan.language not in LOCAL_SERVERS and not hosts:
        return [(api[0], NET, api[1])]
    return []


def _markup(plan: ProjectPlan) -> list[str]:
    """XAML и ресурсы .resx: запуск методов прямо из разметки и сериализованные объекты."""
    out = []
    for f in plan.files:
        ext = PurePosixPath(f.path).suffix.lower()
        pats = {".xaml": [(r"\bObjectDataProvider\b|<x:Code\b", CODE)],
                ".resx": [(r"application/x-microsoft\.net\.object\.(?:binary|soap)", CODE)]}.get(ext, [])
        for pat, what in pats:
            if m := re.search(pat, f.content):
                out.append(f"{f.path}, строка {f.content.count(chr(10), 0, m.start()) + 1}: {what} — {m[0]}")
    return out


def _shots(plan: ProjectPlan) -> list[str]:
    """Сценарий снимка не открывает внешние адреса — только страницы проекта и localhost."""
    out = []
    for shot in plan.shots:
        for url in [shot.url] + [st.value for st in shot.steps if st.action == "open"]:
            if re.match(r"\s*[a-z][a-z0-9+.-]*://", url, re.I) and _host_kind(url) != "local":
                out.append(f"Снимок {shot.section}.{shot.block}: {NET} — {_short(url, 40)}")
    return out


def sql_problems(text: str, own: bool = False) -> list[str]:
    """Опасное в SQL, который кто-то наберёт руками (агент в SSMS, Workbench, консоли mysql): что делает — фрагмент.
    own=False — сервер студента: пользователи, права и чужие базы трогать нельзя."""
    code, strings = lex(text + "\n", "sql")
    src = _Src("ввод", "sql", code, strings, 0, False)
    created = {m[1].lower() for m in SQL_CREATE_DB.finditer(text)}
    return [f"{what} — {snippet}" for _, what, snippet in _sql(src.code, created, src, own=own) + _mysql_client(src)]


def _typed(plan: ProjectPlan) -> list[_Src]:
    """Что ForLabs наберёт в консоли mysql на снимке — тоже запросы (и команды клиента вроде «system …»)."""
    if plan.language != "mysql":
        return []
    out = []
    for shot in plan.shots:
        text = "\n".join([shot.input] + [st.value for st in shot.steps if st.action == "type"]).strip()
        if text:
            code, strings = lex(text + "\n", "sql")
            out.append(_Src(f"Снимок {shot.section}.{shot.block}", "sql", code, strings, 0, False))
    return out


def scan(plan: ProjectPlan) -> list[str]:
    """Почему ForLabs не должен запускать эту программу сам (по-русски). Пустой список — можно запускать."""
    sources = _sources(plan) + _typed(plan)
    created = set()
    for src in sources:
        for text in [src.code if src.lang == "sql" else ""] + [s for _, s, _ in src.strings if SQL_START.match(s)]:
            created |= {m[1].lower() for m in SQL_CREATE_DB.finditer(text)}
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for src in sources:
        for line, what, snippet in sorted(_scan_source(src, plan, created)):
            if (src.path, what) in seen:
                continue
            seen.add((src.path, what))
            where = f"{src.path}, строка {line}" if line else src.path
            out.append(f"{where}: {what} — {snippet}")
    return (out + _markup(plan) + _shots(plan))[:30]
