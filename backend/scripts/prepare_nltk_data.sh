#!/usr/bin/env bash
# 用途：在国内主机（含阿里云 ECS）上一次性准备 NLTK 数据到 host 目录，
#       由 docker-compose volume 挂载到容器，避免运行时联网下载。
#
# 背景与约束（见 notes/LOW_COST_CLOUD_DEPLOYMENT_MANUAL.md 6.1 / 10.2 节）：
#   - ECS 不继承本机 VPN/代理/DNS，访问 raw.githubusercontent.com 会卡死；
#   - 生产 Docker build 不下载 NLTK / 模型权重 / parser 数据等非必要资源；
#   - llama-index-core 在初始化分词器时会自动 nltk.download('punkt_tab' / 'stopwords')，
#     若数据缺失，应用启动会被阻塞 → uvicorn 不监听 → /health 拒连 → 容器 unhealthy。
#
# 使用：
#   bash backend/scripts/prepare_nltk_data.sh [TARGET_DIR]
#   默认 TARGET_DIR=/opt/data/nltk_data
#
# 之后在 docker-compose.prod.yml 中以 SM_NLTK_DATA_ROOT 注入，或使用默认路径。

set -euo pipefail

TARGET_DIR="${1:-/opt/data/nltk_data}"

# 国内可达 CDN（按优先级 fallback；首个能拉通即停止重试）
MIRRORS=(
  "https://cdn.jsdelivr.net/gh/nltk/nltk_data@gh-pages/packages"
  "https://fastly.jsdelivr.net/gh/nltk/nltk_data@gh-pages/packages"
  "https://gh-proxy.com/https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages"
  "https://ghproxy.net/https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages"
)

# 必需资源清单（基于 ECS 启动日志：punkt_tab、stopwords 是 llama-index 强制依赖；
# punkt、wordnet 为历史依赖与 rag_tokenizer 的 NLTK 退路，一并预置避免后续再触网）
PACKAGES=(
  "tokenizers/punkt"
  "tokenizers/punkt_tab"
  "corpora/stopwords"
  "corpora/wordnet"
)

mkdir -p "$TARGET_DIR/tokenizers" "$TARGET_DIR/corpora"

download_one() {
  local rel="$1"
  local cat="${rel%%/*}"
  local name="${rel##*/}"
  local out_dir="$TARGET_DIR/$cat"
  local marker="$out_dir/$name"

  if [[ -d "$marker" || -f "$marker" ]]; then
    echo "[skip] $rel (already present)"
    return 0
  fi

  for mirror in "${MIRRORS[@]}"; do
    local url="$mirror/$cat/$name.zip"
    local tmp
    tmp="$(mktemp /tmp/nltk_${name}_XXXXXX.zip)"
    echo "[try ] $url"
    if curl -fsSL --max-time 60 --retry 1 "$url" -o "$tmp" 2>/dev/null; then
      if python3 -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$tmp" "$out_dir" 2>/dev/null; then
        rm -f "$tmp"
        echo "[ok  ] $rel"
        return 0
      fi
    fi
    rm -f "$tmp"
    echo "[fail] $url"
  done

  echo "[ERR ] failed to download $rel from all mirrors" >&2
  return 1
}

for pkg in "${PACKAGES[@]}"; do
  download_one "$pkg"
done

echo
echo "NLTK data ready at: $TARGET_DIR"
echo "Mount in compose via:"
echo "  - $TARGET_DIR:/usr/local/nltk_data:ro"
ls -la "$TARGET_DIR"
ls -la "$TARGET_DIR/tokenizers" 2>/dev/null || true
ls -la "$TARGET_DIR/corpora" 2>/dev/null || true
