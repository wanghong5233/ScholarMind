# Doc Studio 文档重组脚本
# 功能：移动文件到新的 docs/ 目录结构

Write-Host "开始文档重组..." -ForegroundColor Green

# 1. 移动 RL_TRAINING_DESIGN.md 到 docs/future/
if (Test-Path "RL_TRAINING_DESIGN.md") {
    Move-Item -Path "RL_TRAINING_DESIGN.md" -Destination "docs\future\RL_TRAINING_DESIGN.md" -Force
    Write-Host "[✓] 已移动 RL_TRAINING_DESIGN.md -> docs/future/" -ForegroundColor Green
} else {
    Write-Host "[!] 文件 RL_TRAINING_DESIGN.md 不存在，可能已被移动" -ForegroundColor Yellow
}

# 2. 移动 LaTeX编辑Agent设计.md 到 docs/archived/
if (Test-Path "LaTeX编辑Agent设计.md") {
    Move-Item -Path "LaTeX编辑Agent设计.md" -Destination "docs\archived\LaTeX编辑Agent设计.md" -Force
    Write-Host "[✓] 已移动 LaTeX编辑Agent设计.md -> docs/archived/" -ForegroundColor Green
} else {
    Write-Host "[!] 文件 LaTeX编辑Agent设计.md 不存在，可能已被移动" -ForegroundColor Yellow
}

# 3. 移动 MODEL_ARCHITECTURE.md 到 docs/archived/
if (Test-Path "MODEL_ARCHITECTURE.md") {
    Move-Item -Path "MODEL_ARCHITECTURE.md" -Destination "docs\archived\MODEL_ARCHITECTURE.md" -Force
    Write-Host "[✓] 已移动 MODEL_ARCHITECTURE.md -> docs/archived/" -ForegroundColor Green
} else {
    Write-Host "[!] 文件 MODEL_ARCHITECTURE.md 不存在，可能已被移动" -ForegroundColor Yellow
}

Write-Host "`n文档重组完成！" -ForegroundColor Green
Write-Host "`n新的文档结构：" -ForegroundColor Cyan
Get-ChildItem -Recurse -File "*.md" | Select-Object -Property @{Name="文档路径";Expression={$_.FullName.Replace($PWD.Path + "\", "")}}

