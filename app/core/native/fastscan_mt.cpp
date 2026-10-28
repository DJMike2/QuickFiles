// QuickFiles native scanner, adapted from the supplied fastscan_mt.cpp.
// Retains its Windows NtQueryDirectoryFile / relative NtOpenFile fast path,
// shared directory queue and buffered, synchronized output. POSIX uses
// readdir + fstatat + openat, never following links on either platform.
// Build through: python tools/build_scanner.py
#if !defined(_WIN32) && !defined(__linux__)
#error "QuickFiles supports only Windows and Linux"
#endif
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <winioctl.h>
#include <shellapi.h>
#include <fcntl.h>
#include <io.h>
#else
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <clocale>
#include <codecvt>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <cwctype>
#include <deque>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <locale>
#include <mutex>
#include <regex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

static constexpr int protocol = 1;
static std::wstring wide(const std::string& value) {
#ifdef _WIN32
    std::wstring_convert<std::codecvt_utf8_utf16<wchar_t>> converter;
#else
    std::wstring_convert<std::codecvt_utf8<wchar_t>> converter;
#endif
    return converter.from_bytes(value);
}
static std::string utf8(const std::wstring& value) {
#ifdef _WIN32
    std::wstring_convert<std::codecvt_utf8_utf16<wchar_t>> converter;
#else
    std::wstring_convert<std::codecvt_utf8<wchar_t>> converter;
#endif
    return converter.to_bytes(value);
}
static std::wstring folded(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(), std::towlower);
    return value;
}
static std::string quote(const std::string& value) {
    static const char hex[] = "0123456789abcdef";
    std::string out = "\"";
    out.reserve(value.size() + 2);
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') {
            out += '\\';
            out += char(c);
        }
        else if (c < 32) {
            out += "\\u00";
            out += hex[c >> 4];
            out += hex[c & 15];
        }
        else out += char(c);
    }
    return out + '"';
}

struct Policy {
    std::vector<std::wregex> exclude;
    std::vector<std::wstring> blocked, markers;
} policy;

static std::wstring normalized_path(std::wstring path) {
#ifdef _WIN32
    std::replace(path.begin(), path.end(), L'\\', L'/');
    if (path.rfind(L"//?/UNC/", 0) == 0) path = L"//" + path.substr(8);
    else if (path.rfind(L"//?/", 0) == 0) path = path.substr(4);
#endif
    while (path.size() > 1 && path.back() == L'/') path.pop_back();
    return path;
}

static void read_policy(const std::string& filename) {
#ifdef _WIN32
    std::ifstream in(std::filesystem::path(wide(filename)), std::ios::binary);
#else
    std::ifstream in(filename, std::ios::binary);
#endif
    if (!in) throw std::runtime_error("Cannot open scanner policy");
    std::string key, value;
    while (std::getline(in, key, '\0')) {
        if (!std::getline(in, value, '\0')) throw std::runtime_error("Truncated scanner policy");
        if (key == "exclude") policy.exclude.emplace_back(wide(value), std::regex::ECMAScript | std::regex::optimize);
        else if (key == "blocked") {
            auto path = normalized_path(wide(value));
#ifdef _WIN32
            path = folded(path);
#endif
            policy.blocked.push_back(std::move(path));
        }
        else if (key == "marker") policy.markers.push_back(wide(value));
        else throw std::runtime_error("Unknown scanner policy field");
    }
}
static bool excluded(const std::wstring& name) {
    if (name.empty() || name.front() == L'.') return true;
#ifdef _WIN32
    const auto normalized = folded(name);
#else
    const auto& normalized = name;
#endif
    for (const auto& pattern : policy.exclude)
        if (std::regex_match(normalized, pattern)) return true;
    return false;
}
static bool valid_name(const std::wstring& name) {
    auto whitespace = [](wchar_t c) {
        return std::iswspace(c) || c == 0x85 || c == 0xa0 || c == 0x202f;
    };
    if (name.empty() || name.size() > 255 || whitespace(name.front()) ||
        whitespace(name.back()) || name.back() == L'.') return false;
    for (wchar_t c : name)
        if (c < 32 || std::wstring(L"<>:\"/\\|?*").find(c) != std::wstring::npos) return false;
    auto stem = folded(name.substr(0, name.find(L'.')));
    if (stem == L"con" || stem == L"prn" || stem == L"aux" || stem == L"nul" ||
        stem == L"conin$" || stem == L"conout$") return false;
    return !(stem.size() == 4 && (stem.substr(0, 3) == L"com" || stem.substr(0, 3) == L"lpt") &&
             stem[3] >= L'1' && stem[3] <= L'9');
}
static bool protected_name(const std::wstring& name) {
    auto n = folded(name);
    if (n.rfind(L"~$", 0) == 0 || n == L"desktop.ini" || n == L"thumbs.db" ||
        n == L"ehthumbs.db" || n == L"ntuser.ini") return true;
    for (const auto* stem : {L"ntuser.dat", L"usrclass.dat"}) {
        std::wstring s(stem);
        if (n == s || n.rfind(s + L".", 0) == 0 || n.rfind(s + L"{", 0) == 0) return true;
    }
    return false;
}
static bool blocked(std::wstring path) {
#ifdef _WIN32
    path = folded(path);
#endif
    for (const auto& base : policy.blocked)
        if (path == base || (path.size() > base.size() && path.compare(0, base.size(), base) == 0 && path[base.size()] == L'/')) return true;
    return false;
}
static bool marker(const std::wstring& name) {
    for (const auto& item : policy.markers) if (name == item) return true;
    for (const auto* suffix : {L".sln", L".csproj"}) {
        std::wstring s(suffix);
        if (name.size() >= s.size() && name.compare(name.size() - s.size(), s.size(), s) == 0) return true;
    }
    return false;
}

#ifdef _WIN32
using Dir = HANDLE;
static const Dir invalid_dir = INVALID_HANDLE_VALUE;
using NtStatus = LONG;
struct NtUnicodeString { USHORT Length, MaximumLength; PWSTR Buffer; };
struct NtObjectAttributes { ULONG Length; HANDLE RootDirectory; NtUnicodeString* ObjectName; ULONG Attributes; PVOID a, b; };
struct NtIoStatusBlock { union { NtStatus Status; PVOID Pointer; }; ULONG_PTR Information; };
struct DirInfo {
    ULONG NextEntryOffset, FileIndex;
    LARGE_INTEGER CreationTime, LastAccessTime, LastWriteTime, ChangeTime, EndOfFile, AllocationSize;
    ULONG FileAttributes, FileNameLength;
    WCHAR FileName[1];
};
using QueryDirFn = NtStatus (NTAPI*)(HANDLE, HANDLE, PVOID, PVOID, NtIoStatusBlock*, PVOID, ULONG, int, BOOLEAN, NtUnicodeString*, BOOLEAN);
using OpenFn = NtStatus (NTAPI*)(PHANDLE, ACCESS_MASK, NtObjectAttributes*, NtIoStatusBlock*, ULONG, ULONG);
static QueryDirFn query_dir;
static OpenFn open_dir;
static constexpr ULONG share_all = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
static void close_dir(Dir dir) { CloseHandle(dir); }
#else
using Dir = int;
static constexpr Dir invalid_dir = -1;
static void close_dir(Dir dir) { close(dir); }
#endif

struct Item {
    std::wstring name;
    bool directory = false, link = false, regular = true;
    unsigned long attributes = 0;
    unsigned long long size = 0;
    double modified = 0;
};
struct Task { Dir dir; std::wstring relative; unsigned depth; };
static std::wstring root;
static std::deque<Task> tasks;
static std::mutex queue_mutex, output_mutex;
static std::condition_variable ready;
static size_t outstanding = 0;
static bool finished = false;
static std::atomic<unsigned long long> errors{0}, skipped{0};
static std::atomic<bool> output_failed{false};

static void write_out(std::string& buffer) {
    if (buffer.empty()) return;
    std::lock_guard<std::mutex> lock(output_mutex);
    if (fwrite(buffer.data(), 1, buffer.size(), stdout) != buffer.size() || fflush(stdout) != 0) output_failed = true;
    buffer.clear();
}
static void push_task(Task task) {
    std::lock_guard<std::mutex> lock(queue_mutex);
    tasks.push_back(std::move(task));
    ++outstanding;
    ready.notify_one();
}
static bool list_directory(const Task& task, std::vector<Item>& items) {
#ifdef _WIN32
    // Large directory queries and metadata in the same record, as in the supplied scanner.
    alignas(8) unsigned char buffer[64 * 1024];
    BOOLEAN restart = TRUE;
    for (;;) {
        NtIoStatusBlock io{};
        NtStatus status = query_dir(task.dir, nullptr, nullptr, nullptr, &io, buffer, sizeof(buffer), 1, FALSE, nullptr, restart);
        restart = FALSE;
        if (status == (NtStatus)0x80000006L || status == (NtStatus)0xC000000FL) return true;
        if (status < 0 || !io.Information) return false;
        const unsigned char* at = buffer;
        for (;;) {
            const auto* entry = reinterpret_cast<const DirInfo*>(at);
            std::wstring name(entry->FileName, entry->FileNameLength / sizeof(WCHAR));
            if (name != L"." && name != L"..") {
                Item item;
                item.name = std::move(name);
                item.attributes = entry->FileAttributes;
                item.directory = (item.attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
                item.link = (item.attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
                item.size = item.directory ? 0 : entry->EndOfFile.QuadPart;
                item.modified = double(entry->LastWriteTime.QuadPart) / 10000000.0 - 11644473600.0;
                items.push_back(std::move(item));
            }
            if (!entry->NextEntryOffset) break;
            at += entry->NextEntryOffset;
        }
    }
#else
    int copy = dup(task.dir);
    if (copy < 0) return false;
    DIR* directory = fdopendir(copy);
    if (!directory) {
        close(copy);
        return false;
    }
    bool ok = true;
    for (;;) {
        errno = 0;
        dirent* entry = readdir(directory);
        if (!entry) {
            if (errno) ok = false;
            break;
        }
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        Item item;
        try { item.name = wide(entry->d_name); }
        catch (const std::range_error&) {
            ++skipped;
            continue;
        }
        // Avoid metadata work for files that the policy already excludes.
        if (entry->d_type != DT_DIR && (excluded(item.name) || !valid_name(item.name) || protected_name(item.name))) {
            // Project detection still needs every child name, including hidden markers.
            item.regular = false;
            items.push_back(std::move(item));
            continue;
        }
        struct stat info{};
        if (fstatat(task.dir, entry->d_name, &info, AT_SYMLINK_NOFOLLOW) != 0) {
            ++errors;
            continue;
        }
        item.directory = S_ISDIR(info.st_mode);
        item.link = S_ISLNK(info.st_mode);
        item.regular = S_ISREG(info.st_mode);
        item.size = item.directory ? 0 : info.st_size;
        item.modified = double(info.st_mtim.tv_sec) + double(info.st_mtim.tv_nsec) / 1e9;
        items.push_back(std::move(item));
    }
    closedir(directory);
    return ok;
#endif
}
static Dir child_dir(Dir parent, const std::wstring& name) {
#ifdef _WIN32
    NtUnicodeString unicode{USHORT(name.size() * 2), USHORT(name.size() * 2), const_cast<PWSTR>(name.data())};
    NtObjectAttributes attributes{sizeof(attributes), parent, &unicode, 0, nullptr, nullptr};
    NtIoStatusBlock io{};
    HANDLE child = invalid_dir;
    // Unlike CreateFileW, NtOpenFile does not add FILE_READ_ATTRIBUTES, which the tag check needs.
    if (open_dir(&child, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE, &attributes, &io, share_all,
                 0x00000001 | 0x00000020 | 0x00200000) < 0) return invalid_dir;
    FILE_ATTRIBUTE_TAG_INFO info{};
    if (!GetFileInformationByHandleEx(child, FileAttributeTagInfo, &info, sizeof(info)) ||
        (info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        close_dir(child);
        return invalid_dir;
    }
    return child;
#else
    return openat(parent, utf8(name).c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
#endif
}
static void directory_record(char type, const Task& task, std::string& buffer) {
    buffer += "{\"t\":\"";
    buffer += type;
    buffer += "\",\"p\":" + quote(utf8(task.relative)) + "}\n";
}
static void process(const Task& task, std::string& buffer) {
    std::vector<Item> items;
    bool ok = list_directory(task, items);
    if (!ok) ++errors;
    if (!task.relative.empty()) {
        bool project = !ok || folded(task.relative.substr(task.relative.find_last_of(L'/') + 1)) == L"issetupprerequisites";
        for (const auto& item : items) if (marker(item.name)) {
            project = true;
            break;
        }
        directory_record(project ? 'P' : 'D', task, buffer);
        if (project) return;
    } else if (!ok) throw std::runtime_error("Cannot read selected folder");
    for (const auto& item : items) {
        if (output_failed) return;
        if (excluded(item.name) || !valid_name(item.name) || protected_name(item.name)) {
            if (!item.directory) ++skipped;
            continue;
        }
        const auto relative = task.relative.empty() ? item.name : task.relative + L'/' + item.name;
        if (item.link || blocked(root + L'/' + relative) || (!item.directory &&
            (!item.regular || (item.attributes & (0x4 | 0x1000 | 0x40000 | 0x400000))))) {
            if (!item.directory) ++skipped;
            continue;
        }
        if (item.directory) {
            Dir child = child_dir(task.dir, item.name);
            if (child == invalid_dir) {
                ++errors;
                directory_record('P', {invalid_dir, relative, task.depth + 1}, buffer);
            } else push_task({child, relative, task.depth + 1});
        } else {
            // LC_NUMERIC stays C; only LC_CTYPE is changed at startup.
            char metadata[128];
            snprintf(metadata, sizeof(metadata), ",\"sz\":%llu,\"mt\":%.9f}\n", item.size, item.modified);
            buffer += "{\"t\":\"F\",\"p\":";
            buffer += quote(utf8(relative));
            buffer += metadata;
        }
        if (buffer.size() >= 256 * 1024) write_out(buffer);
    }
}
static void worker() {
    std::string buffer;
    buffer.reserve(256 * 1024);
    for (;;) {
        Task task;
        {
            std::unique_lock<std::mutex> lock(queue_mutex);
            ready.wait(lock, [] { return !tasks.empty() || finished; });
            if (tasks.empty()) break;
            task = std::move(tasks.front());
            tasks.pop_front();
        }
        try { process(task, buffer); }
        catch (const std::exception& exc) {
            std::lock_guard<std::mutex> lock(output_mutex);
            std::cerr << "Scanner worker failed: " << exc.what() << '\n';
            output_failed = true;
        }
        close_dir(task.dir);
        write_out(buffer);
        {
            std::lock_guard<std::mutex> lock(queue_mutex);
            if (--outstanding == 0) {
                finished = true;
                ready.notify_all();
            }
        }
    }
}
static unsigned pick_threads(const std::wstring& path) {
#ifdef _WIN32
    unsigned cap = std::max(1u, std::min(4u, std::thread::hardware_concurrency()));
    // Preserve the supplied scanner's conservative HDD/network-drive selection.
    if (path.size() < 2 || path[1] != L':') return 1;
    std::wstring volume = L"\\\\.\\" + path.substr(0, 2);
    HANDLE device = CreateFileW(volume.c_str(), 0, share_all, nullptr, OPEN_EXISTING, 0, nullptr);
    if (device == INVALID_HANDLE_VALUE) return 1;
    STORAGE_PROPERTY_QUERY query{};
    query.PropertyId = StorageDeviceSeekPenaltyProperty;
    query.QueryType = PropertyStandardQuery;
    DEVICE_SEEK_PENALTY_DESCRIPTOR result{};
    DWORD got = 0;
    bool ok = DeviceIoControl(device, IOCTL_STORAGE_QUERY_PROPERTY, &query, sizeof(query),
                              &result, sizeof(result), &got, nullptr);
    CloseHandle(device);
    return ok && !result.IncursSeekPenalty ? cap : 1;
#else
    (void)path;
    // Unknown storage: avoid parallel random reads by default; --threads can opt in.
    return 1;
#endif
}

int main(int argc, char** argv) {
    try {
        std::setlocale(LC_CTYPE, "");
#ifdef _WIN32
        (void)argc;
        (void)argv;
        _setmode(_fileno(stdout), _O_BINARY);
        int count = 0;
        LPWSTR* command = CommandLineToArgvW(GetCommandLineW(), &count);
        if (!command) throw std::runtime_error("Cannot read command line");
        std::vector<std::string> args;
        for (int i = 0; i < count; ++i) args.push_back(utf8(command[i]));
        LocalFree(command);
#else
        std::vector<std::string> args(argv, argv + argc);
#endif
        if (args.size() == 2 && args[1] == "--version") {
            std::cout << "quickfiles-fastscan " << protocol << '\n';
            return 0;
        }
        if (args.size() != 4) throw std::runtime_error("Usage: fastscan ROOT POLICY THREADS (0 = auto)");
        read_policy(args[2]);
        root = normalized_path(wide(args[1]));
        if (blocked(root)) throw std::runtime_error("Selected folder is protected");
        unsigned threads = std::stoul(args[3]);
        if (!threads) threads = pick_threads(root);
        if (threads > 64) throw std::runtime_error("Thread count must be between 0 and 64");
        Dir directory;
#ifdef _WIN32
        HMODULE nt = GetModuleHandleW(L"ntdll.dll");
        FARPROC query_address = GetProcAddress(nt, "NtQueryDirectoryFile");
        FARPROC open_address = GetProcAddress(nt, "NtOpenFile");
        static_assert(sizeof(query_dir) == sizeof(query_address), "Unexpected function pointer size");
        static_assert(sizeof(open_dir) == sizeof(open_address), "Unexpected function pointer size");
        memcpy(&query_dir, &query_address, sizeof(query_dir));
        memcpy(&open_dir, &open_address, sizeof(open_dir));
        if (!query_dir || !open_dir) throw std::runtime_error("NT directory API unavailable");
        auto native = root;
        std::replace(native.begin(), native.end(), L'/', L'\\');
        if (native.rfind(L"\\\\?\\", 0) != 0)
            native = native.rfind(L"\\\\", 0) == 0 ? L"\\\\?\\UNC\\" + native.substr(2) : L"\\\\?\\" + native;
        directory = CreateFileW(native.c_str(), FILE_LIST_DIRECTORY | SYNCHRONIZE, share_all, nullptr,
                                 OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (directory != invalid_dir) {
            FILE_ATTRIBUTE_TAG_INFO info{};
            if (!GetFileInformationByHandleEx(directory, FileAttributeTagInfo, &info, sizeof(info)) ||
                (info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
                close_dir(directory);
                directory = invalid_dir;
            }
        }
#else
        directory = open(args[1].c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
#endif
        if (directory == invalid_dir) throw std::runtime_error("Cannot open selected folder");
        std::string header = "{\"t\":\"H\",\"protocol\":" + std::to_string(protocol) + ",\"threads\":" + std::to_string(threads) + "}\n";
        write_out(header);
        push_task({directory, L"", 0});
        std::vector<std::thread> pool;
        for (unsigned i = 0; i < threads; ++i) pool.emplace_back(worker);
        for (auto& thread : pool) thread.join();
        if (output_failed) return 2;
        std::string summary = "{\"t\":\"S\",\"skipped\":" + std::to_string(skipped.load()) + ",\"errors\":" + std::to_string(errors.load()) + "}\n";
        write_out(summary);
        return output_failed ? 2 : 0;
    } catch (const std::exception& exc) {
        std::cerr << "fastscan: " << exc.what() << '\n';
        return 1;
    }
}
