# SQL Context Pack one-line setup for Windows.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://github.com/cognivex006/sql-context-pack/releases/latest/download/install.ps1 | iex"
#
# Downloads the latest release bundle, verifies its SHA-256, unpacks it under
# %LOCALAPPDATA%\Programs\sql-context-pack\releases\<version>, runs that release's own
# install.ps1 for each selected host, and then checks package, plugin and service versions.
#
# install.ps1 and update.ps1 differ only in $operation; a contract test keeps them identical
# otherwise. Keep this file ASCII: Windows PowerShell 5.1 decodes `irm` output without a charset.
#
# SQLCTX_HOST=codex,claude  limits the hosts (codex, claude, gemini, agy).
# SQLCTX_DRY_RUN=1          downloads and verifies the release, prints the plan, changes nothing.
& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$operation = 'update'
$repositoryUrl = 'https://github.com/cognivex006/sql-context-pack'
$bundleName = 'sql-context-pack-install.zip'
$hostNames = @('codex', 'claude', 'gemini', 'agy')
# Path.home() in the Python tooling reads USERPROFILE, so the host paths here must too.
$userHome = $env:USERPROFILE
$installRoot = Join-Path $env:LOCALAPPDATA 'Programs\sql-context-pack'
$releasesRoot = Join-Path $installRoot 'releases'
$dryRun = $env:SQLCTX_DRY_RUN -eq '1'
$powershell = if ($PSVersionTable.PSEdition -eq 'Core') { Join-Path $PSHOME 'pwsh.exe' } else { Join-Path $PSHOME 'powershell.exe' }
$state = @{ stage = 'Starting'; log = $null; logPath = $null; tail = New-Object 'System.Collections.Generic.Queue[string]' }

if (-not ('SqlCtxSetupV1.Display' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace SqlCtxSetupV1 {
// Two reserved terminal rows: an overall bar, then a spinner with the current stage.
// A timer keeps the spinner moving while a child installer or a UAC prompt is silent.
public sealed class Display : IDisposable {
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll")] static extern bool GetConsoleMode(IntPtr h, out uint mode);
    [DllImport("kernel32.dll")] static extern bool SetConsoleMode(IntPtr h, uint mode);
    readonly object gate = new object();
    readonly Stopwatch clock = Stopwatch.StartNew();
    readonly string operation;
    readonly Timer timer;
    readonly IntPtr handle;
    readonly uint oldMode;
    readonly bool interactive;
    bool active, closed;
    int percent, tick, printed;
    long received = -1, total = -1;
    string stage = "Starting", detail = "", lastPrinted;

    public Display(string action) {
        operation = action;
        handle = GetStdHandle(-12);
        bool console = !Console.IsErrorRedirected && GetConsoleMode(handle, out oldMode);
        interactive = console && SetConsoleMode(handle, oldMode | 4);
        if (interactive) timer = new Timer(Tick, null, 200, 200);
    }

    void Tick(object ignored) { lock (gate) { if (!closed) Draw(); } }

    // Display-cell clipping that never forwards terminal controls from untrusted text.
    public static string Clip(string text, int limit) {
        text = text ?? "";
        var b = new StringBuilder(); int used = 0;
        for (int i = 0; i < text.Length; i++) {
            if (char.IsSurrogate(text[i]) && !(char.IsHighSurrogate(text[i]) &&
                i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))) {
                if (used + 1 > limit) break;
                b.Append(' '); used++; continue;
            }
            int cp = char.ConvertToUtf32(text, i);
            var cat = CharUnicodeInfo.GetUnicodeCategory(text, i);
            string s = char.ConvertFromUtf32(cp);
            if (cp > 65535) i++;
            int width = 1;
            if (cat == UnicodeCategory.Control || cat == UnicodeCategory.Format || cat == UnicodeCategory.Surrogate) s = " ";
            else if (cat == UnicodeCategory.NonSpacingMark || cat == UnicodeCategory.EnclosingMark || cat == UnicodeCategory.SpacingCombiningMark) width = 0;
            else if (cp >= 0x1100 && (cp <= 0x115f || cp == 0x2329 || cp == 0x232a ||
                (cp >= 0x2e80 && cp <= 0xa4cf) || (cp >= 0xac00 && cp <= 0xd7a3) ||
                (cp >= 0xf900 && cp <= 0xfaff) || (cp >= 0xfe10 && cp <= 0xfe6f) ||
                (cp >= 0xff01 && cp <= 0xff60) || (cp >= 0xffe0 && cp <= 0xffe6) ||
                cp >= 0x1f000)) width = 2;
            if (used + width > limit) break;
            b.Append(s); used += width;
        }
        return b.ToString();
    }

    static string Size(long bytes) {
        if (bytes >= 1048576) return (bytes / 1048576.0).ToString("F1", CultureInfo.InvariantCulture) + " MB";
        return (bytes / 1024) + " KB";
    }

    void Draw() {
        if (closed) return;
        if (!interactive) {
            if (stage != lastPrinted && printed < 40) {
                Console.Error.WriteLine("sql-context-pack " + operation + ": " + Clip(stage, 160));
                lastPrinted = stage; printed++;
            }
            return;
        }
        int width;
        try { width = Math.Max(20, Console.WindowWidth - 1); } catch { width = 79; }
        bool downloading = received >= 0;
        int shown = downloading ? (total > 0 ? (int)Math.Min(100, 100 * received / total) : -1) : percent;
        string label = downloading ? "Downloading" : "sql-context-pack " + operation;
        string suffix = shown >= 0 ? " " + shown + "%" : "";
        if (downloading) suffix += "  " + Size(received) + (total > 0 ? " / " + Size(total) : "");
        int size = Math.Min(30, Math.Max(10, width - label.Length - suffix.Length - 3));
        int filled = shown >= 0 ? shown * size / 100 : tick % size;
        string bar = shown >= 0 ? new string('#', filled) + new string('-', size - filled) :
            new string('-', filled) + "#" + new string('-', size - filled - 1);
        string top = label + " [" + bar + "]" + suffix;
        string seconds = clock.Elapsed.TotalSeconds.ToString("F1", CultureInfo.InvariantCulture);
        string bottom = "|/-\\"[tick++ % 4] + " " + seconds + "s  " + stage + (detail.Length > 0 ? " - " + detail : "");
        if (!active) { Console.Error.Write("\u001b[?25l\r\n"); active = true; }
        // The cursor always rests on the second reserved row, at column one.
        Console.Error.Write("\r\u001b[1A\u001b[2K" + Clip(top, width) + "\r\n\u001b[2K" + Clip(bottom, width) + "\r");
        Console.Error.Flush();
    }

    public void Update(int value, string text) {
        lock (gate) {
            if (closed) return;
            text = text ?? "";
            if (text != stage) { clock.Restart(); detail = ""; }
            percent = Math.Max(percent, Math.Max(0, Math.Min(100, value)));
            stage = text; received = -1; total = -1;
            Draw();
        }
    }

    public void Detail(string text) {
        lock (gate) { if (!closed) detail = text ?? ""; }
    }

    public void Transfer(long count, long length, string text) {
        lock (gate) {
            if (closed) return;
            bool changed = text != stage || received < 0;
            if (changed) { clock.Restart(); detail = ""; }
            received = count; total = length; stage = text ?? "";
            if (changed) Draw();
        }
    }

    public void Dispose() {
        lock (gate) {
            if (closed) return;
            closed = true;
            if (timer != null) timer.Dispose();
            if (active) {
                Console.Error.Write("\r\u001b[2K\u001b[1A\r\u001b[2K\u001b[?25h");
                Console.Error.Flush(); active = false;
            }
            if (interactive) SetConsoleMode(handle, oldMode);
        }
    }
}

// Runs one child with stdout and stderr merged line by line, without PowerShell turning
// native stderr into terminating errors. Stdin is closed so a prompt fails instead of hanging.
public sealed class Child : IDisposable {
    readonly Process process = new Process();
    readonly Queue<string> lines = new Queue<string>();
    readonly StringBuilder output = new StringBuilder();
    int open = 2;

    public Child(string file, string arguments, string directory) {
        var info = process.StartInfo;
        info.FileName = file;
        info.Arguments = arguments;
        info.WorkingDirectory = directory;
        info.UseShellExecute = false;
        info.RedirectStandardInput = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.StandardOutputEncoding = Encoding.UTF8;
        info.StandardErrorEncoding = Encoding.UTF8;
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
        info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        info.EnvironmentVariables["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
        info.EnvironmentVariables["PIP_NO_INPUT"] = "1";
        // A PowerShell 7 module path breaks a Windows PowerShell child, and the reverse;
        // without one, each edition starts from its own default.
        info.EnvironmentVariables.Remove("PSModulePath");
        process.Start();
        process.StandardInput.Close();
        Pump(process.StandardOutput, true);
        Pump(process.StandardError, false);
    }

    void Pump(StreamReader reader, bool stdout) {
        var thread = new Thread(() => {
            try {
                string line;
                while ((line = reader.ReadLine()) != null) {
                    lock (lines) {
                        lines.Enqueue(line);
                        if (stdout) output.AppendLine(line);
                        Monitor.PulseAll(lines);
                    }
                }
            } catch (IOException) {
            } finally {
                lock (lines) { open--; Monitor.PulseAll(lines); }
            }
        });
        thread.IsBackground = true;
        thread.Start();
    }

    // The next merged line, or null once both streams have ended.
    public string ReadLine() {
        lock (lines) {
            while (lines.Count == 0 && open > 0) Monitor.Wait(lines);
            return lines.Count > 0 ? lines.Dequeue() : null;
        }
    }

    public string Output { get { lock (lines) { return output.ToString(); } } }

    public int Wait() { process.WaitForExit(); return process.ExitCode; }

    public void Dispose() {
        try { if (!process.HasExited) process.Kill(); } catch (Exception) { }
        process.Dispose();
    }

    // Quote for CommandLineToArgvW, which powershell.exe and python.exe both use.
    public static string Join(string[] arguments) {
        var parts = new string[arguments.Length];
        for (int i = 0; i < arguments.Length; i++) parts[i] = Quote(arguments[i]);
        return string.Join(" ", parts);
    }

    static string Quote(string value) {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return value;
        var b = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { b.Append('\\', slashes * 2 + 1); b.Append('"'); }
            else { b.Append('\\', slashes); b.Append(c); }
            slashes = 0;
        }
        b.Append('\\', slashes * 2);
        b.Append('"');
        return b.ToString();
    }
}
}
'@
}

$display = New-Object SqlCtxSetupV1.Display($operation)

function Step([int]$Percent, [string]$Text) {
    $state.stage = $Text
    $display.Update($Percent, $Text)
}

function Write-Log([string]$Line) {
    if ($state.log) { $state.log.WriteLine($Line) }
    $state.tail.Enqueue($Line)
    while ($state.tail.Count -gt 20) { [void]$state.tail.Dequeue() }
}

function Find-Python {
    # The same order as the release's python-preflight.ps1, so both select one interpreter.
    # No double quotes in the probe: Windows PowerShell 5.1 strips them from native arguments.
    $ErrorActionPreference = 'Continue'
    $probe = 'import sys, importlib.util as u; sys.exit(1) if sys.version_info < (3, 11) or u.find_spec(''pip'') is None else print(sys.executable)'
    foreach ($candidate in @('py -3', 'python3', 'python')) {
        $parts = $candidate -split ' '
        $executable = $parts[0]
        if (-not (Get-Command $executable -ErrorAction SilentlyContinue)) { continue }
        $arguments = @($parts | Select-Object -Skip 1) + @('-I', '-B', '-c', $probe)
        try { $found = & $executable @arguments 2>$null } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $found) { continue }
        $path = ([string]($found | Select-Object -Last 1)).Trim()
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    }
    return $null
}

function Get-Destination([string]$HostName) {
    if ($HostName -eq 'gemini') { return Join-Path $userHome '.gemini\extensions\sql-context-pack' }
    if ($HostName -eq 'agy') { return Join-Path $userHome '.gemini\config\skills\sql-context-pack' }
    return Join-Path $userHome 'plugins\sql-context-pack'
}

function Get-Registrations([string]$HostName) {
    # Plugin ids this host already lists sql-context-pack under, read from the host's own
    # state files: `codex plugin list` can take over a minute to answer.
    $ids = @()
    if ($HostName -eq 'codex') {
        $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $userHome '.codex' }
        $config = Join-Path $codexHome 'config.toml'
        if (Test-Path -LiteralPath $config) {
            $ids = @(Select-String -LiteralPath $config -Pattern '^\s*\[plugins\.["''](sql-context-pack@[^"'']+)["'']\]' |
                ForEach-Object { $_.Matches[0].Groups[1].Value })
        }
    } elseif ($HostName -eq 'claude') {
        $claudeHome = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $userHome '.claude' }
        $installed = Join-Path $claudeHome 'plugins\installed_plugins.json'
        if (Test-Path -LiteralPath $installed) {
            try { $document = Get-Content -Raw -LiteralPath $installed | ConvertFrom-Json } catch { $document = $null }
            if ($document -and $document.plugins) {
                $ids = @($document.plugins.PSObject.Properties.Name | Where-Object { $_ -like 'sql-context-pack@*' })
            }
        }
    }
    return $ids | Select-Object -Unique
}

function Test-Installed([string]$HostName) {
    if ($HostName -in @('codex', 'claude')) { return @(Get-Registrations $HostName).Count -gt 0 }
    return Test-Path -LiteralPath (Get-Destination $HostName)
}

function Select-Hosts {
    if ($env:SQLCTX_HOST) {
        $selected = @($env:SQLCTX_HOST.ToLowerInvariant() -split '[\s,;]+' | Where-Object { $_ } | Select-Object -Unique)
        $unknown = @($selected | Where-Object { $_ -notin $hostNames })
        if ($selected.Count -eq 0 -or $unknown.Count -gt 0) {
            throw "SQLCTX_HOST must name codex, claude, gemini or agy; it is '$($env:SQLCTX_HOST)'."
        }
        return $selected
    }
    $installed = @($hostNames | Where-Object { Test-Installed $_ })
    if ($operation -eq 'update') {
        if ($installed.Count -eq 0) {
            throw 'SQL Context Pack is not installed for codex, claude, gemini or agy. Run install.ps1 first, or set SQLCTX_HOST.'
        }
        return $installed
    }
    $found = @($hostNames | Where-Object { $_ -in $installed -or (Get-Command $_ -ErrorAction SilentlyContinue) })
    if ($found.Count -eq 0) {
        throw 'No supported host CLI (codex, claude, agy or gemini) is on PATH. Install one, or set SQLCTX_HOST, then run this again.'
    }
    return $found
}

function Get-Plan([string]$HostName) {
    # A registration from another marketplace (the GitHub marketplace, or a developer's
    # local checkout) stays the owner's: only the package and service are installed for it,
    # so the host never lists sql-context-pack twice.
    $foreign = @(@(Get-Registrations $HostName) | Where-Object { $_ -ne 'sql-context-pack@personal' })
    $mode = if ($HostName -eq 'agy') { 'skill' } elseif ($foreign.Count -gt 0) { 'native' } else { 'personal' }
    $registration = if ($foreign.Count -gt 0) { $foreign[0] } elseif ($HostName -in @('codex', 'claude')) { 'sql-context-pack@personal' } else { $null }
    return [pscustomobject]@{
        Host = $HostName
        Mode = $mode
        Registration = $registration
        Update = Test-Path -LiteralPath (Get-Destination $HostName)
    }
}

function Get-RefreshCommand($Plan) {
    $marketplace = $Plan.Registration.Split('@')[1]
    if ($Plan.Host -eq 'codex') { return "codex plugin marketplace upgrade $marketplace" }
    return "claude plugin marketplace update $marketplace; claude plugin install $($Plan.Registration)"
}

function New-Request([string]$Url, [bool]$Follow) {
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.UserAgent = 'sql-context-pack-setup'
    $request.Timeout = 60000
    $request.ReadWriteTimeout = 60000
    $request.AllowAutoRedirect = $Follow
    return $request
}

function Resolve-LatestTag {
    # Pin one tag first so the bundle and its checksum always come from the same release.
    $response = (New-Request "$repositoryUrl/releases/latest" $false).GetResponse()
    try { $location = [string]$response.Headers['Location'] } finally { $response.Close() }
    if ($location -match '/releases/tag/(v\d+\.\d+\.\d+)$') { return $Matches[1] }
    throw 'No published SQL Context Pack release was found.'
}

function Receive-File([string]$Url, [string]$Destination, [long]$Limit, [string]$Label) {
    $display.Transfer(0, -1, $Label)
    try { $response = (New-Request $Url $true).GetResponse() }
    catch {
        $failure = $_.Exception
        while ($failure -and $failure -isnot [Net.WebException]) { $failure = $failure.InnerException }
        if ($failure -and $failure.Response -and [int]$failure.Response.StatusCode -eq 404) {
            throw "The latest release has no $Label asset yet. Existing installation was not changed."
        }
        throw
    }
    $source = $null; $target = $null
    try {
        [long]$total = $response.ContentLength
        if ($total -gt $Limit) { throw "$Label is larger than the setup limit." }
        $source = $response.GetResponseStream()
        $target = [IO.File]::Create($Destination)
        $buffer = New-Object byte[] 65536
        [long]$received = 0
        while (($count = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $received += $count
            if ($received -gt $Limit) { throw "$Label is larger than the setup limit." }
            $target.Write($buffer, 0, $count)
            $display.Transfer($received, $total, $Label)
        }
        if ($total -ge 0 -and $received -ne $total) { throw "$Label ended before all declared bytes arrived." }
    } finally {
        if ($target) { $target.Dispose() }
        if ($source) { $source.Dispose() }
        $response.Close()
    }
}

function Test-Bundle([string]$Archive) {
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        if ($zip.Entries.Count -gt 20000) { throw 'The release bundle has too many entries.' }
        [long]$size = 0
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '(^[/\\]|(^|[/\\])\.\.([/\\]|$)|:|\\)') { throw 'The release bundle contains an unsafe path.' }
            $size += $entry.Length
        }
        if ($size -gt 536870912) { throw 'The release bundle expands beyond the setup limit.' }
    } finally { $zip.Dispose() }
}

function Assert-Release([string]$Root, [string]$Version) {
    foreach ($relative in @('install.ps1', 'scripts\install-global.ps1', 'scripts\global_install.py', '.codex-plugin\plugin.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relative) -PathType Leaf)) { throw "The release bundle is missing $relative." }
    }
    $declared = (Get-Content -Raw -LiteralPath (Join-Path $Root '.codex-plugin\plugin.json') | ConvertFrom-Json).version
    if ($declared -ne $Version) { throw "Release v$Version declares product version $declared." }
}

function Install-Release([string]$Archive, [string]$Digest, [string]$Version) {
    $target = Join-Path $releasesRoot $Version
    $marker = Join-Path $target '.sqlctx-bundle-sha256'
    if ((Test-Path -LiteralPath $marker) -and ((Get-Content -Raw -LiteralPath $marker).Trim() -eq $Digest)) { return $target }
    New-Item -ItemType Directory -Path $releasesRoot -Force | Out-Null
    $stage = Join-Path $releasesRoot ('.stage-' + [Guid]::NewGuid().ToString('N'))
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $stage)
    Assert-Release $stage $Version
    [IO.File]::WriteAllText((Join-Path $stage '.sqlctx-bundle-sha256'), $Digest)
    if (Test-Path -LiteralPath $target) {
        $previous = Join-Path $releasesRoot ('.old-' + [Guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $target -Destination $previous
        Remove-Item -LiteralPath $previous -Recurse -Force -ErrorAction SilentlyContinue
    }
    Move-Item -LiteralPath $stage -Destination $target
    return $target
}

function Remove-UnusedReleases([string]$Current) {
    # Install provenance records the release each host was installed from, and repair reads
    # it back, so a release some host still points at is kept.
    $kept = @([IO.Path]::GetFullPath($Current).TrimEnd('\'))
    foreach ($name in $hostNames) {
        $provenance = Join-Path (Get-Destination $name) '.sqlctx-install.json'
        if (Test-Path -LiteralPath $provenance) {
            try { $kept += [IO.Path]::GetFullPath((Get-Content -Raw -LiteralPath $provenance | ConvertFrom-Json).source_root).TrimEnd('\') } catch { }
        }
    }
    Get-ChildItem -LiteralPath $releasesRoot -Directory -Force | Where-Object { $_.FullName.TrimEnd('\') -notin $kept } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

function Read-InstallerLine([string]$Line) {
    # Map the release installer's own stage markers onto the display.
    $text = $Line.Trim()
    if (-not $text -or $text.StartsWith('[timing]')) { return $null }
    if ($text -match '^\[build\] Cache hit') { return @{ Stage = 'Reusing the cached application wheel'; Share = 0.15 } }
    if ($text -match '^\[build\]') { return @{ Stage = 'Building the application wheel'; Share = 0.05 } }
    if ($text -match '^\[package\] Cache hit') { return @{ Stage = 'Python package is already current'; Share = 0.4 } }
    if ($text -match '^\[package\]') { return @{ Stage = 'Installing the Python package'; Share = 0.25 } }
    if ($text -match '^\[plugin\]') { return @{ Stage = 'Plugin stays with its host marketplace'; Share = 0.55 } }
    if ($text -match '^\{.*"ok": true') { return @{ Stage = 'Plugin registered with the host'; Share = 0.55 } }
    if ($text -match '^Preparing the managed Windows Service') { return @{ Stage = 'Installing the Windows Service'; Share = 0.65 } }
    if ($text -match 'Administrator access is required') { return @{ Stage = 'Waiting for Administrator approval (UAC; check the taskbar if no prompt appears)'; Share = 0.7 } }
    if ($text -match 'service restart skipped') { return @{ Stage = 'Windows Service is already current'; Share = 0.95 } }
    if ($text -match '^(Installation is ready|Inside Codex|The service and plugin files|PATH-independent|The current PowerShell)') { return $null }
    if ($text -match '^\[SQL Context Pack\] (.+)$') { return @{ Detail = $Matches[1] } }
    return @{ Detail = $text }
}

function Invoke-Child([string]$File, [string[]]$Arguments, [int]$From, [int]$To, [string]$Label, [string]$Directory) {
    Step $From $Label
    Write-Log "### $Label"
    $child = New-Object SqlCtxSetupV1.Child($File, [SqlCtxSetupV1.Child]::Join($Arguments), $Directory)
    try {
        while ($null -ne ($line = $child.ReadLine())) {
            Write-Log $line
            $parsed = Read-InstallerLine $line
            if (-not $parsed) { continue }
            if ($parsed.Stage) { Step ($From + [int](($To - $From) * $parsed.Share)) $parsed.Stage }
            else { $display.Detail($parsed.Detail) }
        }
        $code = $child.Wait()
        $output = $child.Output
    } finally { $child.Dispose() }
    if ($code -ne 0) { throw "$Label failed (exit $code)." }
    return $output
}

function Invoke-Host($Plan, [string]$Release, [string]$Python, [int]$From, [int]$To) {
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Release 'install.ps1'), '-SkipConfigure')
    if ($Plan.Mode -eq 'personal') {
        $arguments += @('-Harness', $Plan.Host)
        if ($Plan.Update) { $arguments += '-Update' }
    } else {
        # Package and service only. -Harness is unused with -NativePlugin, and the release
        # install.ps1 cannot take agy, whose skill files global_install.py places below.
        $arguments += @('-Harness', 'codex', '-NativePlugin')
    }
    $middle = if ($Plan.Mode -eq 'skill') { $From + [int](($To - $From) * 0.8) } else { $To }
    # Children start in the private temp folder, never the folder the owner ran this from.
    [void](Invoke-Child $powershell $arguments $From $middle "Installing for $($Plan.Host)" $temp)
    if ($Plan.Mode -eq 'skill') {
        $verb = if ($Plan.Update) { 'update' } else { 'install' }
        $skillArguments = @('-B', (Join-Path $Release 'scripts\global_install.py'), $verb, '--harness', 'agy', '--mode', 'skill', '--source-root', $Release)
        [void](Invoke-Child $Python $skillArguments $middle $To 'Placing the agy skill and MCP entry' $temp)
    }
}

function Test-Host($Plan, [string]$Python, [string]$Version, [string]$Directory, [int]$Percent) {
    $label = "Checking $($Plan.Host) versions"
    Step $Percent $label
    Write-Log "### $label"
    $child = New-Object SqlCtxSetupV1.Child($Python, [SqlCtxSetupV1.Child]::Join(@('-B', '-m', 'sqlctx.cli', 'doctor', 'check', '--host', $Plan.Host)), $Directory)
    try {
        while ($null -ne ($line = $child.ReadLine())) { Write-Log $line }
        $code = $child.Wait()
        $output = $child.Output
    } finally { $child.Dispose() }
    $json = @($output -split "`r?`n" | Where-Object { $_.StartsWith('{') }) | Select-Object -Last 1
    if (-not $json) { throw "sqlctx doctor check --host $($Plan.Host) returned no report (exit $code)." }
    $report = $json | ConvertFrom-Json
    $versions = $report.versions
    $result = [pscustomobject]@{ Host = $Plan.Host; Plugin = $versions.plugin; Problems = @(); Notes = @(); Profiles = $report.profiles.configured }
    if ($versions.package -ne $Version) { $result.Problems += "package is $($versions.package), expected $Version" }
    if ($versions.service -ne $Version) { $result.Problems += "service is $($versions.service), expected $Version" }
    if ($null -eq $versions.plugin) {
        $staged = if ($versions.staged_plugin -eq $Version) { "; staged plugin files are $Version" } else { '' }
        $result.Notes += "$($Plan.Host): the host CLI did not report the plugin version$staged. Check with: sqlctx doctor check --host $($Plan.Host)"
    } elseif ($versions.plugin -ne $Version) {
        if ($Plan.Mode -eq 'native') {
            $result.Notes += "$($Plan.Host): plugin $($versions.plugin) comes from $($Plan.Registration). Refresh it with: $(Get-RefreshCommand $Plan)"
        } else {
            $result.Problems += "$($Plan.Host) plugin is $($versions.plugin), expected $Version"
        }
    }
    foreach ($finding in @($report.findings)) {
        if ($finding.code -and $finding.code -notin @('VERSION_DRIFT', 'PLUGIN_VERSION_UNVERIFIED')) {
            $result.Notes += "$($Plan.Host): doctor reports $($finding.code)"
        }
    }
    if ($code -ne 0) { $result.Notes += "$($Plan.Host): sqlctx doctor check exited $code; run it to see why." }
    return $result
}

$temp = Join-Path ([IO.Path]::GetTempPath()) ('sqlctx-setup-' + [Guid]::NewGuid().ToString('N'))
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Path $temp | Out-Null
    Step 2 'Checking for Python 3.11+ with pip'
    $python = Find-Python
    if (-not $python) {
        throw 'Python 3.11 or newer with pip was not found. Install it from https://www.python.org/downloads/windows/, then run this again.'
    }
    Step 5 'Selecting hosts'
    $plans = @(Select-Hosts | ForEach-Object { Get-Plan $_ })
    Step 8 'Finding the latest release'
    $tag = Resolve-LatestTag
    $version = $tag.Substring(1)
    $base = "$repositoryUrl/releases/download/$tag"
    $archive = Join-Path $temp $bundleName
    Receive-File "$base/$bundleName" $archive 67108864 "$bundleName ($tag)"
    Step 40 'Downloading the checksum'
    Receive-File "$base/$bundleName.sha256" "$archive.sha256" 4096 "$bundleName.sha256"
    Step 44 'Verifying SHA-256'
    $checksum = [IO.File]::ReadAllText("$archive.sha256")
    if ($checksum -notmatch '^([a-f0-9]{64})  sql-context-pack-install\.zip\s*$') { throw 'The release checksum file is invalid.' }
    $digest = $Matches[1]
    # .NET rather than Get-FileHash: Windows PowerShell started under PowerShell 7's module
    # path cannot load that cmdlet.
    $stream = [IO.File]::OpenRead($archive)
    try { $actual = -join ([Security.Cryptography.SHA256]::Create().ComputeHash($stream) | ForEach-Object { $_.ToString('x2') }) }
    finally { $stream.Dispose() }
    if ($actual -ne $digest) {
        throw 'Release checksum mismatch. Existing installation was not changed.'
    }
    Step 48 'Checking bundle contents'
    Test-Bundle $archive

    if ($dryRun) {
        Step 50 'Unpacking for inspection'
        $release = Join-Path $temp 'release'
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $release)
        Assert-Release $release $version
        $display.Dispose()
        Write-Host "Dry run: SQL Context Pack $version ($tag) downloaded and verified. Nothing was installed."
        Write-Host "Python: $python"
        Write-Host "Release folder: $(Join-Path $releasesRoot $version)"
        foreach ($plan in $plans) {
            $action = if ($plan.Update) { 'update' } else { 'install' }
            $what = switch ($plan.Mode) {
                'native' { "package and service only; the plugin stays $($plan.Registration) from its own marketplace" }
                'skill' { "$action package, service, skill files and MCP entry" }
                default { if ($plan.Registration) { "$action package, service and plugin $($plan.Registration)" } else { "$action package, service and extension" } }
            }
            Write-Host "  $($plan.Host): $what"
        }
        return
    }

    Step 50 "Unpacking $version"
    $release = Install-Release $archive $digest $version
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'logs') -Force | Out-Null
    $state.logPath = Join-Path $installRoot "logs\$operation.log"
    $state.log = New-Object IO.StreamWriter($state.logPath, $false, (New-Object Text.UTF8Encoding($false)))
    $state.log.AutoFlush = $true
    Write-Log "sql-context-pack $operation $version from $tag; python $python"

    $band = [Math]::Floor(38 / $plans.Count)
    for ($index = 0; $index -lt $plans.Count; $index++) {
        Invoke-Host $plans[$index] $release $python (55 + $index * $band) (55 + ($index + 1) * $band)
    }
    $results = @()
    for ($index = 0; $index -lt $plans.Count; $index++) {
        $results += Test-Host $plans[$index] $python $version $temp (94 + [Math]::Floor($index * 5 / $plans.Count))
    }
    $problems = @($results | ForEach-Object { $_.Problems })
    if ($problems.Count -gt 0) { throw ('Verification failed: ' + ($problems -join '; ')) }

    Step 99 'Finishing'
    Remove-UnusedReleases $release
    # install-global.ps1 adds the user Scripts folder to the user PATH; this shell needs it now.
    try { $scripts = [string](& $python -c 'import sysconfig; print(sysconfig.get_path(''scripts'', scheme=''nt_user''))') } catch { $scripts = $null }
    if ($scripts -and (($env:Path -split ';') -notcontains $scripts.Trim())) { $env:Path = "$($scripts.Trim());$env:Path" }
    Step 100 'Ready'
    $display.Dispose()

    $notes = @($results | ForEach-Object { $_.Notes })
    Write-Host "SQL Context Pack $version is ready." -ForegroundColor Green
    Write-Host "  package $version, service $version (127.0.0.1)"
    foreach ($result in $results) {
        $plugin = if ($result.Plugin) { $result.Plugin } else { 'not reported' }
        Write-Host "  $($result.Host): plugin $plugin"
    }
    foreach ($note in $notes) { Write-Host "  ! $note" -ForegroundColor Yellow }
    Write-Host 'Next:'
    Write-Host '  1. Open a new session in your host; Skill instructions load when a session starts.'
    if (@($results | Where-Object { $_.Profiles -eq 0 }).Count -gt 0) {
        Write-Host '  2. Create a database profile in this terminal: sqlctx profile configure'
    }
    foreach ($result in $results) {
        if ($result.Host -eq 'codex') { Write-Host '     Codex: $sql-context-pack profiles' }
        elseif ($result.Host -eq 'claude') { Write-Host '     Claude Code: /sql-context-pack:sql-context-pack profiles' }
        else { Write-Host "     $($result.Host): ask 'Use the sql-context-pack skill to list profiles.'" }
    }
    Write-Host "Log: $($state.logPath)"
} catch {
    $display.Dispose()
    Write-Host "SQL Context Pack $operation failed while: $([SqlCtxSetupV1.Display]::Clip($state.stage, 160))" -ForegroundColor Red
    if ($state.tail.Count -gt 0) {
        Write-Host 'Last installer output:'
        foreach ($line in $state.tail) { Write-Host ('  ' + [SqlCtxSetupV1.Display]::Clip($line, 200)) }
    }
    if ($state.logPath) { Write-Host "Full log: $($state.logPath)" }
    if (@($state.tail | Where-Object { $_ -match 'cancell?ed by the user' }).Count -gt 0) {
        Write-Host 'Administrator approval was declined, so the Windows Service was not installed. Run this again and approve the UAC prompt.' -ForegroundColor Yellow
    }
    throw
} finally {
    $display.Dispose()
    if ($state.log) { $state.log.Dispose() }
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
}
