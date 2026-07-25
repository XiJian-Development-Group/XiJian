<#
.SYNOPSIS
    XiJian Core API — 一条龙开发脚本 (Windows PowerShell)

.DESCRIPTION
    功能：检测并准备 conda 环境 → 安装/更新依赖 → (可选)编译 AI 后端 →
    (可选)运行测试 → (可选)启动服务 → (可选)打包为独立可执行文件。
    所有步骤均可单独开关，支持交互式向导和纯命令行两种模式。

    未被本脚本识别的参数（如 --port / --dev / --log-level / --host …）
    会在 -Run 时原样转发给 `python -m xijian_api`。

.EXAMPLE
    # 不传任何参数 → 进入交互式向导（推荐新手）
    .\core\scripts\dev.ps1

.EXAMPLE
    # 显式进入交互式向导
    .\core\scripts\dev.ps1 -Interactive

.EXAMPLE
    # 安装 + 跑测试 + 以 dev 模式启动在 18600 端口
    .\core\scripts\dev.ps1 -Test -Run -Dev -Port 18600

.EXAMPLE
    # 安装并编译 GGUF 后端（llama-cpp-python），使用本地源码路径
    .\core\scripts\dev.ps1 -WithGguf -GgufPath D:\code\llama-cpp-python

.EXAMPLE
    # 打包为独立可执行文件（onedir 模式）
    .\core\scripts\dev.ps1 -Build

.EXAMPLE
    # 打包后自动生成 zip 分发包
    .\core\scripts\dev.ps1 -Build -Zip

.EXAMPLE
    # 显示当前环境与配置信息
    .\core\scripts\dev.ps1 -Info

.EXAMPLE
    # 指定 conda 环境名（默认 xijianBase）
    .\core\scripts\dev.ps1 -Env myenv -Run -Dev
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Env = "xijianBase",
    [string]$PyVersion = "3.12",
    [switch]$NoInstall,
    [switch]$WithMlx,
    [switch]$WithGguf,
    [string]$MlxPath = "",
    [string]$GgufPath = "",
    [switch]$Test,
    [switch]$Run,
    [switch]$Build,
    [switch]$Zip,
    [switch]$Clean,
    [switch]$Info,
    [switch]$Dev,
    [string]$Port,
    [string]$Host_,
    [string]$LogLevel,
    [string]$LogFile,
    [string]$Config,
    [switch]$NoServe,
    [switch]$Interactive,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

# -----------------------------------------------------------------------------
# 路径解析（脚本可从任意目录调用）
# -----------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CoreDir = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $CoreDir

function Write-Log  { param([string]$m) Write-Host "[xijian] $m" -ForegroundColor Cyan }
function Write-Warn { param([string]$m) Write-Host "[xijian WARN] $m" -ForegroundColor Yellow }
function Write-Err  { param([string]$m) Write-Host "[xijian ERROR] $m" -ForegroundColor Red }
function Write-Ok   { param([string]$m) Write-Host "[xijian OK] $m" -ForegroundColor Green }
function Write-Step { param([string]$m) Write-Host "[xijian STEP] $m" -ForegroundColor Magenta }

# -----------------------------------------------------------------------------
# 交互式输入辅助函数
# -----------------------------------------------------------------------------
function Prompt-YesNo {
    param([string]$Question, [string]$Default = "Y")
    $hint = if ($Default -match '^[Yy]') { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        Write-Host "[xijian Q] $Question $hint " -ForegroundColor Cyan -NoNewline
        $reply = Read-Host
        if ([string]::IsNullOrWhiteSpace($reply)) { $reply = $Default }
        switch -Regex ($reply.Substring(0,1).ToUpper()) {
            "Y" { return $true }
            "N" { return $false }
        }
    }
}

function Prompt-Input {
    param([string]$Question, [string]$Default = "")
    $suffix = if ($Default) { " (默认: $Default)" } else { "" }
    Write-Host "[xijian Q] $Question$suffix " -ForegroundColor Cyan -NoNewline
    $reply = Read-Host
    if ([string]::IsNullOrWhiteSpace($reply) -and $Default) { return $Default }
    return $reply
}

function Prompt-Choice {
    param([string]$Question, [int]$DefaultIdx = 1, [string[]]$Options)
    while ($true) {
        Write-Host "[xijian Q] $Question" -ForegroundColor Cyan
        for ($i = 0; $i -lt $Options.Count; $i++) {
            $marker = if (($i+1) -eq $DefaultIdx) { "*" } else { " " }
            Write-Host "  $marker $($i+1)) $($Options[$i])"
        }
        Write-Host "请输入序号 (默认 $DefaultIdx): " -NoNewline
        $reply = Read-Host
        if ([string]::IsNullOrWhiteSpace($reply)) { $reply = "$DefaultIdx" }
        $n = 0
        if ([int]::TryParse($reply, [ref]$n) -and $n -ge 1 -and $n -le $Options.Count) {
            return $n
        }
        Write-Warn "无效输入: $reply"
    }
}

# -----------------------------------------------------------------------------
# 判断是否进入交互模式：未传任何命名参数且未显式 -Interactive
# -----------------------------------------------------------------------------
$boundCount = $PSBoundParameters.Count
if ($boundCount -eq 0 -and -not $Interactive) {
    $script:Interactive = $true
}

# -----------------------------------------------------------------------------
# -Info: 显示环境信息后退出
# -----------------------------------------------------------------------------
if ($Info) {
    Write-Log "XiJian Core API 环境信息"
    Write-Log "  项目根目录  : $ProjectDir"
    Write-Log "  core 目录   : $CoreDir"
    Write-Log "  scripts 目录: $ScriptDir"
    Write-Log "  conda 环境  : $Env"
    Write-Log "  Python 版本 : $PyVersion"
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $pyVer = & python -V 2>&1
        Write-Log "  当前 Python : $pyVer ($($pyCmd.Source))"
    }
    if (Test-Path "$CoreDir\config.toml") {
        Write-Log "  配置文件    : $CoreDir\config.toml"
    }
    exit 0
}

# -----------------------------------------------------------------------------
# 交互式向导
# -----------------------------------------------------------------------------
function Run-Interactive {
    $bar = "=" * 60
    Write-Log $bar
    Write-Log "XiJian Core API 交互式启动向导"
    Write-Log "未提供参数，进入交互模式。可随时用 Ctrl+C 退出。"
    Write-Log $bar

    # 1. conda 环境
    $script:Env = Prompt-Input "conda 环境名" $script:Env

    # 2. 安装依赖
    $script:NoInstall = -not (Prompt-YesNo "是否安装/更新核心依赖？" "Y")

    # 3. AI 后端
    if (Prompt-YesNo "是否安装 GGUF 后端 (llama-cpp-python)？" "N") {
        $script:WithGguf = $true
        if (Prompt-YesNo "  使用本地源码路径安装？" "N") {
            $script:GgufPath = Prompt-Input "  llama-cpp-python 源码路径" ""
        }
    }
    # Windows 上 MLX 不可用，但仍提供选项以防未来跨平台支持
    if (Prompt-YesNo "是否安装 MLX 后端 (仅 macOS Apple Silicon)？" "N") {
        $script:WithMlx = $true
        if (Prompt-YesNo "  使用本地源码路径安装？" "N") {
            $script:MlxPath = Prompt-Input "  mlx-lm 源码路径" ""
        }
    }

    # 4. 测试
    $script:Test = Prompt-YesNo "是否运行测试套件 (pytest)？" "N"

    # 5. 操作选择
    $ops = @("仅启动服务", "仅打包为可执行文件", "打包并生成 zip 分发包", "启动 + 打包", "都不执行（仅准备环境）")
    $choice = Prompt-Choice "选择要执行的操作" 1 $ops
    switch ($choice) {
        1 { $script:Run = $true }
        2 { $script:Build = $true }
        3 { $script:Build = $true; $script:Zip = $true }
        4 { $script:Run = $true; $script:Build = $true }
        5 { }
    }

    # 6. 启动参数
    if ($script:Run) {
        $script:Dev = Prompt-YesNo "  开发模式 (自动生成 token、启用测试路由)？" "Y"
        $script:Port = Prompt-Input "  监听端口" "18500"
        $script:Host_ = Prompt-Input "  监听地址" "0.0.0.0"
        $levels = @("DEBUG","INFO","WARNING","ERROR","CRITICAL")
        $idx = Prompt-Choice "  日志级别" 2 $levels
        $script:LogLevel = $levels[$idx - 1]
        if (Prompt-YesNo "  写入日志文件？" "N") {
            $script:LogFile = Prompt-Input "  日志文件路径" "C:\tmp\xijian.log"
        }
        if (Prompt-YesNo "  指定自定义配置文件？" "N") {
            $script:Config = Prompt-Input "  config.toml 路径" ""
        }
        $script:NoServe = Prompt-YesNo "  仅冒烟自检 (--no-serve，不真正启动)？" "N"
    }

    # 7. 打包选项
    if ($script:Build) {
        $script:Zip = Prompt-YesNo "  打包后自动生成 zip 分发包？" "Y"
        $script:Clean = Prompt-YesNo "  打包前清理构建缓存 (build/ dist/)？" "Y"
    }

    # 8. 确认
    Write-Log $bar
    Write-Log "即将执行的操作:"
    Write-Host "  - conda 环境      : $($script:Env)"
    Write-Host "  - 安装依赖        : $(-not $script:NoInstall)"
    if ($script:WithMlx) { Write-Host "  - MLX 后端        : 是$($(if($script:MlxPath){" ($($script:MlxPath))"}))" }
    if ($script:WithGguf) { Write-Host "  - GGUF 后端       : 是$($(if($script:GgufPath){" ($($script:GgufPath))"}))" }
    if ($script:Test) { Write-Host "  - 运行测试        : 是" }
    if ($script:Run) {
        $serveArgs = @()
        if ($script:Dev) { $serveArgs += "--dev" }
        if ($script:Port) { $serveArgs += "--port $($script:Port)" }
        if ($script:Host_) { $serveArgs += "--host $($script:Host_)" }
        if ($script:LogLevel) { $serveArgs += "--log-level $($script:LogLevel)" }
        if ($script:LogFile) { $serveArgs += "--log-file $($script:LogFile)" }
        if ($script:Config) { $serveArgs += "--config $($script:Config)" }
        if ($script:NoServe) { $serveArgs += "--no-serve" }
        Write-Host "  - 启动服务        : 是 ($($serveArgs -join ' '))"
    }
    if ($script:Build) { Write-Host "  - 打包可执行文件  : 是" }
    if ($script:Zip) { Write-Host "  - 生成 zip 分发包 : 是" }
    if ($script:Clean) { Write-Host "  - 清理构建缓存    : 是" }
    Write-Log $bar
    if (-not (Prompt-YesNo "确认执行？" "Y")) {
        Write-Warn "用户取消，退出。"
        exit 0
    }
}

if ($Interactive) { Run-Interactive }

# -----------------------------------------------------------------------------
# conda 检测
# -----------------------------------------------------------------------------
function Find-Conda {
    $cmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($cmd) {
        $exe = $cmd.Source
        if ($exe -like "*.bat") {
            $root = Split-Path -Parent (Split-Path -Parent $exe)
            return @{ Exe = $exe; Root = $root }
        }
        return @{ Exe = $exe; Root = $null }
    }
    $candidates = @(
        "$env:USERPROFILE\anaconda3",
        "$env:USERPROFILE\miniconda3",
        "$env:USERPROFILE\miniforge3",
        "$env:USERPROFILE\mambaforge",
        "C:\ProgramData\anaconda3",
        "C:\ProgramData\miniconda3",
        "C:\ProgramData\miniforge3"
    )
    foreach ($c in $candidates) {
        $exe = Join-Path $c "Scripts\conda.exe"
        if (Test-Path $exe) { return @{ Exe = $exe; Root = $c } }
    }
    return $null
}

$conda = Find-Conda
if (-not $conda) {
    Write-Err "未找到 conda，请先安装 Anaconda/Miniconda/Miniforge 并加入 PATH。"
    exit 1
}
Write-Log "检测到 conda: $($conda.Exe)"

# 加载 PowerShell hook 以获得 conda 函数
$hookScript = & $conda.Exe "shell.powershell" "hook" 2>$null | Out-String
if ($hookScript) {
    try { Invoke-Expression $hookScript } catch {
        Write-Warn "conda PowerShell hook 加载失败 ($_)，尝试直接调用 conda.exe"
    }
}

# 检查环境是否存在
function Test-CondaEnv([string]$name) {
    $list = & $conda.Exe "env" "list" 2>$null | Out-String
    return ($list -split "`n" | ForEach-Object { ($_ -split '\s+')[0] } | Where-Object { $_ -eq $name }).Count -gt 0
}

if (Test-CondaEnv $Env) {
    Write-Log "激活已存在的 conda 环境: $Env"
} else {
    Write-Warn "conda 环境 [$Env] 不存在，正在创建 (python=$PyVersion) ..."
    & $conda.Exe "create" "-n" $Env "python=$PyVersion" "-y" | Out-Null
    Write-Ok "已创建 conda 环境: $Env"
}

# 激活环境
if (Get-Command conda -ErrorAction SilentlyContinue) {
    conda activate $Env
} else {
    $pyExe = if ($conda.Root) {
        Join-Path $conda.Root "envs\$Env\python.exe"
    } else {
        $root = & $conda.Exe "info" "--base" 2>$null | Out-String | ForEach-Object { $_.Trim() }
        Join-Path $root "envs\$Env\python.exe"
    }
    if (-not (Test-Path $pyExe)) { Write-Err "找不到环境内的 python: $pyExe"; exit 1 }
    $env:PATH = "$($conda.Root)\envs\$Env;$($conda.Root)\envs\$Env\Scripts;" + $env:PATH
    Set-Alias -Name python -Value $pyExe -Scope Script
}

Write-Log "Python: $(python -V 2>&1)  ($(Get-Command python).Source)"

# -----------------------------------------------------------------------------
# 安装核心依赖
# -----------------------------------------------------------------------------
Set-Location $CoreDir

if (-not $NoInstall) {
    Write-Step "安装/更新核心依赖 (pip install -e `".[test]`") ..."
    python -m pip install -e ".[test]"
    Write-Ok "核心依赖就绪"
} else {
    Write-Log "跳过依赖安装 (-NoInstall)"
}

# -----------------------------------------------------------------------------
# 可选：编译 AI 后端
# -----------------------------------------------------------------------------
if ($WithMlx) {
    Write-Step "安装 MLX 后端 ..."
    if ($MlxPath) {
        Write-Log "使用本地源码路径: $MlxPath"
        try { python -m pip install -e $MlxPath }
        catch { Write-Warn "本地 mlx-lm 安装失败，回退到 PyPI 版本"; python -m pip install mlx-lm }
    } else {
        python -m pip install mlx-lm
    }
    Write-Ok "MLX 后端安装完成"
}

if ($WithGguf) {
    Write-Step "安装 GGUF 后端 (llama-cpp-python) ..."
    if ($GgufPath) {
        Write-Log "使用本地源码路径: $GgufPath"
        try { python -m pip install -e $GgufPath }
        catch { Write-Warn "本地 llama-cpp-python 安装失败，回退到 PyPI 版本"; python -m pip install llama-cpp-python }
    } else {
        # Windows CUDA 加速建议:
        #   $env:CMAKE_ARGS="-DGGUF_CUDA=on"; python -m pip install llama-cpp-python
        python -m pip install llama-cpp-python
    }
    Write-Ok "GGUF 后端安装完成"
}

# -----------------------------------------------------------------------------
# 可选：运行测试
# -----------------------------------------------------------------------------
if ($Test) {
    Write-Step "运行测试套件 (pytest -q) ..."
    python -m pytest -q
    Write-Ok "测试通过"
}

# -----------------------------------------------------------------------------
# 可选：打包为独立可执行文件
# -----------------------------------------------------------------------------
function Build-Executable {
    Write-Step "打包为独立可执行文件 (PyInstaller onedir 模式) ..."

    # 1. 确保 pyinstaller 已安装
    $pyiInstalled = $false
    try { python -c "import PyInstaller" 2>$null; $pyiInstalled = $true } catch {}
    if (-not $pyiInstalled) {
        Write-Log "安装 PyInstaller ..."
        python -m pip install pyinstaller
    }

    # 2. 清理旧构建
    if ($Clean) {
        Write-Log "清理构建缓存 (build/ dist/) ..."
        if (Test-Path "$CoreDir\build") { Remove-Item -Recurse -Force "$CoreDir\build" }
        if (Test-Path "$CoreDir\dist") { Remove-Item -Recurse -Force "$CoreDir\dist" }
    }

    # 3. 运行 PyInstaller
    Write-Log "运行 PyInstaller ..."
    Set-Location $CoreDir
    python -m PyInstaller scripts\xijian-api.spec --noconfirm --clean

    $distDir = "$CoreDir\dist\xijian-api"
    if (-not (Test-Path $distDir)) {
        Write-Err "打包失败：未找到 $distDir"
        exit 1
    }
    Write-Ok "可执行文件已生成: $distDir"

    # 4. 拷贝配置文件和说明文档
    Write-Log "拷贝配置文件和说明文档 ..."
    if (Test-Path "$CoreDir\config.toml") {
        Copy-Item "$CoreDir\config.toml" "$distDir\config.toml" -Force
        Write-Log "  已拷贝 config.toml"
    }
    if (Test-Path "$ScriptDir\dist-readme.txt") {
        Copy-Item "$ScriptDir\dist-readme.txt" "$distDir\README.txt" -Force
        Write-Log "  已拷贝 README.txt"
    }

    # 5. 创建空目录
    New-Item -ItemType Directory -Force -Path "$distDir\logs" | Out-Null
    New-Item -ItemType Directory -Force -Path "$distDir\run" | Out-Null
    New-Item -ItemType Directory -Force -Path "$distDir\data" | Out-Null
    New-Item -ItemType Directory -Force -Path "$distDir\external_libs" | Out-Null
    Write-Log "  已创建运行时目录 (logs/ run/ data/ external_libs/)"

    # 6. 显示构建产物大小
    $size = (Get-ChildItem -Recurse $distDir | Measure-Object -Property Length -Sum).Sum / 1MB
    Write-Ok ("打包完成，产物大小: {0:N1} MB" -f $size)
    Write-Log "  产物位置: $distDir"

    # 7. 可选：生成 zip 分发包
    if ($Zip) {
        Write-Step "生成 zip 分发包 ..."
        $archTag = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
        $zipName = "xijian-core-windows-$archTag.zip"
        $zipPath = "$CoreDir\dist\$zipName"

        # 删除旧 zip
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

        # 使用 PowerShell 内置 Compress-Archive
        Set-Location "$CoreDir\dist"
        Compress-Archive -Path "xijian-api\*" -DestinationPath $zipPath -Force

        $zipSize = (Get-Item $zipPath).Length / 1MB
        Write-Ok ("zip 分发包已生成: $zipPath ({0:N1} MB)" -f $zipSize)
        Write-Log "  UI 程序解压后运行: xijian-api\xijian-api.exe --port 18500"
    }
}

if ($Build) { Build-Executable }

# -----------------------------------------------------------------------------
# 可选：启动服务
# -----------------------------------------------------------------------------
if ($Run) {
    $serverArgs = @()
    if ($Dev)    { $serverArgs += "--dev" }
    if ($Port)   { $serverArgs += "--port"; $serverArgs += $Port }
    if ($Host_)  { $serverArgs += "--host"; $serverArgs += $Host_ }
    if ($LogLevel) { $serverArgs += "--log-level"; $serverArgs += $LogLevel }
    if ($LogFile)  { $serverArgs += "--log-file"; $serverArgs += $LogFile }
    if ($Config)   { $serverArgs += "--config"; $serverArgs += $Config }
    if ($NoServe)  { $serverArgs += "--no-serve" }
    if ($Rest)     { $serverArgs += $Rest }
    Write-Log "启动 XiJian Core API，参数: $($serverArgs -join ' ')"
    & python -m xijian_api @serverArgs
} elseif (-not $Build -and -not $Info) {
    Write-Ok "环境准备完成。常用命令:"
    Write-Log "  .\core\scripts\dev.ps1 -Run -Dev -Port 18600"
    Write-Log "  .\core\scripts\dev.ps1 -Run -Dev -LogLevel DEBUG -LogFile C:\tmp\xijian.log"
    Write-Log "  .\core\scripts\dev.ps1 -Build -Zip          # 打包为可执行文件"
    Write-Log "  .\core\scripts\dev.ps1 -Info                # 显示环境信息"
    Write-Log "  .\core\scripts\dev.ps1 -Interactive         # 交互式向导"
}
