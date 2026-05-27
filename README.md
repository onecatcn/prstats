# PaddlePaddle 社区 PR 统计工具

本包含两套脚本，推荐优先使用**老脚本**。

---

## 目录结构

```
pr-stats-package/
├── 新脚本/          ← 推荐，基于 GitHub API，无需本地仓库
│   ├── pr_stats.py
│   ├── repos.yaml
│   └── contributors.csv
├── 老脚本/          ← 基于 git log，需要本地 clone 各仓库
│   ├── pr_tool/
│   ├── config.yaml
│   └── release-notes-drafter/
└── 社区开发者数据/   ← 2026 年历史周报（可作为参考）
```

---

## 新脚本（推荐）

### 安装依赖

```bash
pip install requests openpyxl pyyaml
```

### 配置 GitHub Token

```bash
# 方式1：环境变量
export GITHUB_TOKEN=ghp_xxx

# 方式2：写入文件
echo "github_oauth = ghp_xxx" > ~/.gh_tokenrc
```

### 首次使用

将 `新脚本/` 目录整体放到你的工作目录，然后运行：

```bash
# 预览（不写文件）
python3 pr_stats.py --annual-file "/path/to/社区开发者pr统计（2026）.xlsx" --contributors contributors.csv --dry-run

# 正式写入（自动从年度文件检测上次截止日期）
python3 pr_stats.py --annual-file "/path/to/社区开发者pr统计（2026）.xlsx" --contributors contributors.csv

# 手动指定日期范围
python3 pr_stats.py --annual-file "..." --start 2026-04-10 --end 2026-04-16
```

### 年度文件

脚本需要一个年度累积 Excel 文件（每个仓库一个 sheet，每周追加数据 + 截止标记行）。
首次使用需自行创建，或从 `社区开发者数据/commitlist_repo_.xlsx`（模板）复制一份。

---

## 老脚本

> **注意：老脚本存在系统性漏 PR 问题**（git commit 时间戳 ≠ merge 时间，导致部分 PR 漏统计），
> 推荐仅作参考或历史对比使用。

### 前提：clone 各仓库

老脚本需要 15 个 PaddlePaddle 仓库的本地 clone，**脚本不会自动 clone**，需要手动执行：

```bash
# 在 config.yaml 同级目录下执行
for repo in Paddle docs PaddleScience PaddleMIX Paddle2ONNX PaddleOCR PaddleSpeech PaddleNLP FastDeploy GraphNet PaddleCustomDevice PaddleFormers PaddleX PaddleSOT PaConvert; do
    git clone https://github.com/PaddlePaddle/$repo.git
done
```

各仓库 clone 完成后，还需要为每个仓库创建 `get_pr/` 目录结构（从 `release-notes-drafter/get_pr/` 复制 3 个脚本）：

```bash
for repo in Paddle docs PaddleScience PaddleMIX Paddle2ONNX PaddleOCR PaddleSpeech PaddleNLP FastDeploy GraphNet PaddleCustomDevice PaddleFormers PaddleX PaddleSOT PaConvert; do
    mkdir -p $repo/get_pr/results
    cp release-notes-drafter/get_pr/commitlist.py $repo/get_pr/
    cp release-notes-drafter/get_pr/common.py $repo/get_pr/
    cp release-notes-drafter/get_pr/update_repo_pr_list.py $repo/get_pr/
done
```

### 修改 config.yaml

`config.yaml` 中 `base_path` 默认留空（自动检测为 config.yaml 所在目录），**无需修改路径**，
但要确保所有仓库目录和 config.yaml 在**同一个目录**下：

```
你的工作目录/
├── config.yaml          ← 放这里
├── pr_tool/
├── Paddle/              ← clone 的仓库
├── docs/
├── FastDeploy/
├── ...（其余12个仓库）
└── 社区开发者数据/       ← 输出目录（自动创建）
```

### 安装依赖

```bash
pip install pandas openpyxl pyyaml requests
```

### 运行

```bash
# 全流程（sync → collect → export）
python -m pr_tool pipeline 2026.04.10-2026.04.16

# 每周自动执行（自动检测上次截止日，补齐到本周四）
python -m pr_tool weekly
python -m pr_tool weekly --dry-run   # 仅预览
```

---

## contributors.csv 说明

人员分类表，格式：

```csv
github_id,category
alice-dev,社区
bob-baidu,部门内
carol-hw,硬件
```

6 个分类：`社区` / `部门内` / `部门外` / `硬件` / `非硬件生态相关` / `AI Coding`

发现新贡献者时，脚本会自动追加到此文件（标记为 `待分类`），手动更新分类后再次运行即可。
