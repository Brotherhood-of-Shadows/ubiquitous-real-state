/*
 * Native Win32 x86_64 Portable Launcher for Imob Pato Branco (ImobPatoBranco.exe).
 *
 * Freestanding PE64 console executable (zero CRT dependencies; imports only
 * kernel32.dll and user32.dll) that launches the bundled PSF-signed Windows
 * Python runtime (`runtime\python.exe -m imob.cli`) with UTF-8 I/O enabled.
 */

typedef unsigned short wchar_t_win;
typedef unsigned int DWORD;
typedef int BOOL;
typedef unsigned int UINT;
typedef unsigned long long ULONG_PTR;
typedef void *HANDLE;
typedef void *LPVOID;
typedef const wchar_t_win *LPCWSTR;
typedef wchar_t_win *LPWSTR;

#define NULL_PTR ((void *)0)
#define TRUE_VAL 1
#define FALSE_VAL 0
#define INVALID_FILE_ATTRIBUTES ((DWORD)0xFFFFFFFF)
#define INFINITE_WAIT ((DWORD)0xFFFFFFFF)
#define CTRL_C_EVENT 0
#define MB_OK_ICONWARNING ((UINT)0x00000030)
#define MAX_BUF 4096
#define CMD_BUF 8192

typedef struct _STARTUPINFOW {
    DWORD cb;
    LPWSTR lpReserved;
    LPWSTR lpDesktop;
    LPWSTR lpTitle;
    DWORD dwX;
    DWORD dwY;
    DWORD dwXSize;
    DWORD dwYSize;
    DWORD dwXCountChars;
    DWORD dwYCountChars;
    DWORD dwFillAttribute;
    DWORD dwFlags;
    unsigned short wShowWindow;
    unsigned short cbReserved2;
    unsigned char *lpReserved2;
    HANDLE hStdInput;
    HANDLE hStdOutput;
    HANDLE hStdError;
} STARTUPINFOW;

typedef struct _PROCESS_INFORMATION {
    HANDLE hProcess;
    HANDLE hThread;
    DWORD dwProcessId;
    DWORD dwThreadId;
} PROCESS_INFORMATION;

typedef BOOL (*PHANDLER_ROUTINE)(DWORD CtrlType);

__declspec(dllimport) DWORD GetModuleFileNameW(HANDLE hModule, LPWSTR lpFilename, DWORD nSize);
__declspec(dllimport) LPWSTR GetCommandLineW(void);
__declspec(dllimport) DWORD GetFileAttributesW(LPCWSTR lpFileName);
__declspec(dllimport) BOOL SetEnvironmentVariableW(LPCWSTR lpName, LPCWSTR lpValue);
__declspec(dllimport) BOOL SetConsoleCtrlHandler(PHANDLER_ROUTINE HandlerRoutine, BOOL Add);
__declspec(dllimport) BOOL SetConsoleOutputCP(UINT wCodePageID);
__declspec(dllimport) BOOL SetConsoleTitleW(LPCWSTR lpConsoleTitle);
__declspec(dllimport) BOOL CreateProcessW(
    LPCWSTR lpApplicationName,
    LPWSTR lpCommandLine,
    LPVOID lpProcessAttributes,
    LPVOID lpThreadAttributes,
    BOOL bInheritHandles,
    DWORD dwCreationFlags,
    LPVOID lpEnvironment,
    LPCWSTR lpCurrentDirectory,
    STARTUPINFOW *lpStartupInfo,
    PROCESS_INFORMATION *lpProcessInformation
);
__declspec(dllimport) DWORD WaitForSingleObject(HANDLE hHandle, DWORD dwMilliseconds);
__declspec(dllimport) BOOL GetExitCodeProcess(HANDLE hProcess, DWORD *lpExitCode);
__declspec(dllimport) BOOL CloseHandle(HANDLE hObject);
__declspec(dllimport) void ExitProcess(UINT uExitCode);
__declspec(dllimport) int MessageBoxW(HANDLE hWnd, LPCWSTR lpText, LPCWSTR lpCaption, UINT uType);

static void zero_bytes(void *dst, unsigned long long count) {
    volatile unsigned char *p = (volatile unsigned char *)dst;
    while (count > 0) {
        *p++ = 0;
        count--;
    }
}

static void wcopy(LPWSTR dst, LPCWSTR src, unsigned long long cap) {
    unsigned long long i = 0;
    if (cap == 0) {
        return;
    }
    while (i + 1 < cap && src[i] != 0) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = 0;
}

static void wappend(LPWSTR dst, LPCWSTR src, unsigned long long cap) {
    unsigned long long i = 0;
    unsigned long long j = 0;
    while (i < cap && dst[i] != 0) {
        i++;
    }
    while (i + 1 < cap && src[j] != 0) {
        dst[i++] = src[j++];
    }
    if (i < cap) {
        dst[i] = 0;
    }
}

static LPCWSTR skip_argv0(LPCWSTR cmdline) {
    LPCWSTR p = cmdline;
    if (p == NULL_PTR) {
        return L"";
    }
    while (*p == L' ' || *p == L'\t') {
        p++;
    }
    if (*p == L'"') {
        p++;
        while (*p != 0 && *p != L'"') {
            p++;
        }
        if (*p == L'"') {
            p++;
        }
    } else {
        while (*p != 0 && *p != L' ' && *p != L'\t') {
            p++;
        }
    }
    while (*p == L' ' || *p == L'\t') {
        p++;
    }
    return p;
}

static BOOL ctrl_handler(DWORD ctrl_type) {
    if (ctrl_type == CTRL_C_EVENT) {
        /* Let child python.exe handle Ctrl+C cleanly before exiting */
        return TRUE_VAL;
    }
    return FALSE_VAL;
}

static wchar_t_win exe_path[MAX_BUF];
static wchar_t_win app_dir[MAX_BUF];
static wchar_t_win py_exe[MAX_BUF];
static wchar_t_win cmd_line[CMD_BUF];
static STARTUPINFOW si;
static PROCESS_INFORMATION pi;

void entry_point(void) {
    DWORD len;
    long long last_sep = -1;
    long long idx;
    LPCWSTR extra_args;
    DWORD exit_code = 1;

    zero_bytes(exe_path, sizeof(exe_path));
    zero_bytes(app_dir, sizeof(app_dir));
    zero_bytes(py_exe, sizeof(py_exe));
    zero_bytes(cmd_line, sizeof(cmd_line));
    zero_bytes(&si, sizeof(si));
    zero_bytes(&pi, sizeof(pi));

    SetConsoleOutputCP(65001);
    SetConsoleTitleW(L"Imob Pato Branco - Painel de Imoveis");

    len = GetModuleFileNameW(NULL_PTR, exe_path, MAX_BUF - 1);
    if (len == 0 || len >= MAX_BUF - 1) {
        MessageBoxW(
            NULL_PTR,
            L"Nao foi possivel localizar o caminho do executavel.",
            L"Imob Pato Branco",
            MB_OK_ICONWARNING
        );
        ExitProcess(1);
    }

    for (idx = 0; idx < (long long)len; idx++) {
        if (exe_path[idx] == L'\\' || exe_path[idx] == L'/') {
            last_sep = idx;
        }
    }
    if (last_sep <= 0) {
        ExitProcess(1);
    }

    for (idx = 0; idx < last_sep; idx++) {
        app_dir[idx] = exe_path[idx];
    }
    app_dir[last_sep] = 0;

    wcopy(py_exe, app_dir, MAX_BUF);
    wappend(py_exe, L"\\runtime\\python.exe", MAX_BUF);

    if (GetFileAttributesW(py_exe) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(
            NULL_PTR,
            L"Arquivos internos nao encontrados (pasta 'runtime').\r\n\r\n"
            L"Por favor, extraia TODOS os arquivos do .zip para uma pasta antes de abrir o ImobPatoBranco.exe.",
            L"Imob Pato Branco - Extraia o .zip primeiro",
            MB_OK_ICONWARNING
        );
        ExitProcess(2);
    }

    SetEnvironmentVariableW(L"IMOB_APP_ROOT", app_dir);
    SetEnvironmentVariableW(L"IMOB_PORTABLE", L"1");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");

    extra_args = skip_argv0(GetCommandLineW());

    wcopy(cmd_line, L"\"", CMD_BUF);
    wappend(cmd_line, py_exe, CMD_BUF);
    wappend(cmd_line, L"\" -m imob.cli", CMD_BUF);
    if (extra_args != NULL_PTR && extra_args[0] != 0) {
        wappend(cmd_line, L" ", CMD_BUF);
        wappend(cmd_line, extra_args, CMD_BUF);
    }

    SetConsoleCtrlHandler(ctrl_handler, TRUE_VAL);

    si.cb = (DWORD)sizeof(si);
    if (!CreateProcessW(
            py_exe,
            cmd_line,
            NULL_PTR,
            NULL_PTR,
            TRUE_VAL,
            0,
            NULL_PTR,
            app_dir,
            &si,
            &pi)) {
        MessageBoxW(
            NULL_PTR,
            L"Falha ao iniciar o servidor interno do Imob Pato Branco.",
            L"Imob Pato Branco",
            MB_OK_ICONWARNING
        );
        ExitProcess(3);
    }

    WaitForSingleObject(pi.hProcess, INFINITE_WAIT);
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    if (exit_code != 0) {
        MessageBoxW(
            NULL_PTR,
            L"O Imob Pato Branco encerrou com erro.\r\n\r\n"
            L"Veja os detalhes na janela preta do console (atras deste aviso) "
            L"ou no arquivo 'startup-error.log' na pasta do programa.",
            L"Imob Pato Branco - Erro ao iniciar",
            MB_OK_ICONWARNING
        );
    }
    ExitProcess((UINT)exit_code);
}
