# MinerU GPU 加速配置指南

## 📋 前提条件

在启用 GPU 加速之前，请确保满足以下条件：

### 1. 硬件要求
- NVIDIA GPU（支持 CUDA 11.8 或更高版本）
- 推荐显存：至少 6GB

### 2. 软件要求

#### Windows + WSL2（推荐）
- Windows 10/11（版本 21H2 或更高）
- WSL2 已启用
- 最新的 NVIDIA 显卡驱动（Windows 宿主机）
- Docker Desktop for Windows（最新版）

#### Linux
- Ubuntu 20.04/22.04 或其他主流发行版
- NVIDIA 显卡驱动（版本 >= 525.60.13）
- Docker Engine（版本 >= 19.03）
- NVIDIA Container Toolkit

---

## 🚀 安装步骤

### Windows + WSL2 用户

1. **安装 NVIDIA 驱动（Windows 宿主机）**
   - 访问 [NVIDIA 官网](https://www.nvidia.com/Download/index.aspx)
   - 下载并安装最新的 Game Ready 或 Studio 驱动
   - 重启电脑

2. **验证 WSL2 中的 GPU 可用性**
   ```bash
   # 在 WSL2 终端中运行
   nvidia-smi
   ```
   如果能看到 GPU 信息，说明配置成功。

3. **Docker Desktop 会自动支持 GPU**
   - 确保 Docker Desktop 版本 >= 4.0
   - 在设置中启用 WSL2 集成

### Linux 用户

1. **安装 NVIDIA 驱动**
   ```bash
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install -y nvidia-driver-525
   sudo reboot
   
   # 验证
   nvidia-smi
   ```

2. **安装 NVIDIA Container Toolkit**
   ```bash
   # 添加 NVIDIA 软件包存储库
   distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
     sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   
   curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
     sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
     sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   
   # 安装
   sudo apt-get update
   sudo apt-get install -y nvidia-container-toolkit
   
   # 配置 Docker
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```

3. **验证 Docker GPU 支持**
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
   ```
   如果能看到 GPU 信息，说明配置成功。

---

## 🔧 启动 MinerU GPU 版本

### 1. 重新构建镜像
```bash
cd backend

# 停止并删除旧容器
docker-compose stop mineru
docker-compose rm -f mineru

# 重新构建 GPU 版本
docker-compose build mineru

# 启动服务
docker-compose up -d mineru
```

### 2. 验证 GPU 使用

#### 检查健康状态
```bash
curl http://localhost:8001/health
```

期望输出：
```json
{
  "status": "ok",
  "service": "mineru",
  "gpu_available": true,
  "device": "cuda:0"
}
```

#### 查看日志
```bash
docker-compose logs mineru --tail 50
```

期望看到：
```
[MinerU] GPU检测: gpu_available=True, device=cuda:0
INFO:     Started server process [1]
INFO:     Uvicorn running on http://0.0.0.0:8001
```

#### 监控 GPU 使用情况
```bash
# 在宿主机运行
watch -n 1 nvidia-smi

# 或在容器内运行
docker exec scholarmind_mineru nvidia-smi
```

---

## 🐛 常见问题排查

### 问题1：`docker: Error response from daemon: could not select device driver "" with capabilities: [[gpu]]`

**原因**：NVIDIA Container Toolkit 未安装或未配置。

**解决**：
- Linux：按照上面的步骤安装 NVIDIA Container Toolkit
- Windows：确保 Docker Desktop 版本 >= 4.0 且已启用 WSL2 集成

### 问题2：`free(): double free detected in tcache 2`

**原因**：CPU 版 PyTorch 与 GPU 运行时混用。

**解决**：确保使用 `Dockerfile.gpu`，它会先安装 GPU 版 PyTorch。

### 问题3：`CUDA error: no kernel image is available for execution`

**原因**：CUDA 版本不匹配。

**解决**：
1. 检查宿主机 CUDA 版本：`nvidia-smi` 右上角
2. 修改 `Dockerfile.gpu` 中的 PyTorch 版本以匹配 CUDA 版本

### 问题4：容器启动后 `gpu_available=false`

**原因**：GPU 未正确传递给容器。

**解决**：
1. 检查 `docker-compose.yml` 中的 `deploy.resources.reservations.devices` 配置
2. 验证宿主机 GPU 可用：`nvidia-smi`
3. 重启 Docker 服务

### 问题5：显存不足（OOM）

**解决**：
1. 减少并发处理的 PDF 数量
2. 在 `config.py` 中调整 `SM_MINERU_MAX_PAGES`
3. 使用更小的批处理大小

---

## 📊 性能对比

| 场景 | CPU 模式 | GPU 模式 | 加速比 |
|------|----------|----------|--------|
| 18页学术论文 | ~180秒 | ~60秒 | 3x |
| 50页技术报告 | ~500秒 | ~150秒 | 3.3x |
| 扫描版PDF | ~300秒 | ~90秒 | 3.3x |

> 注：实际性能取决于 GPU 型号、PDF 复杂度等因素。

---

## 🔄 切换回 CPU 模式

如果遇到问题或不需要 GPU 加速，可以切换回 CPU 模式：

1. **修改 `docker-compose.yml`**
   ```yaml
   mineru:
     build:
       dockerfile: Dockerfile  # 改回原始 Dockerfile
     # 注释掉 deploy 部分
     # deploy:
     #   resources:
     #     reservations:
     #       devices:
     #         - driver: nvidia
     #           count: 1
     #           capabilities: [gpu]
   ```

2. **重新构建**
   ```bash
   docker-compose build mineru
   docker-compose up -d mineru
   ```

---

## 📚 参考资料

- [NVIDIA Container Toolkit 官方文档](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- [Docker Compose GPU 支持](https://docs.docker.com/compose/gpu-support/)
- [MinerU 官方仓库](https://github.com/opendatalab/MinerU)
- [PyTorch CUDA 安装指南](https://pytorch.org/get-started/locally/)

---

## ✅ 验证清单

在提交代码前，请确认：

- [ ] `nvidia-smi` 在宿主机可用
- [ ] `docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi` 成功
- [ ] `curl http://localhost:8001/health` 返回 `"gpu_available": true`
- [ ] 上传测试 PDF 后，日志中显示 GPU 使用情况
- [ ] `docker exec scholarmind_mineru nvidia-smi` 显示进程占用显存

---

**最后更新**：2025-10-28  
**维护者**：ScholarMind Team

