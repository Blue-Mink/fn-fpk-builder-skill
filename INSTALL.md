# 安装：fn-fpk-builder（跨 Agent）

fnOS FPK 构建/打包/审计/部署工具链技能。技能目录名 `fn-fpk-builder` 是其它技能引用的名字
（frontmatter 里写的是 `fn-fpk-builder-skill`）。

## 一键安装

```bash
S=<本技能目录>/scripts/install.sh
sh $S --list
sh $S --target claude-code --apply
sh $S --target codex --apply
sh $S --target cursor --apply
sh $S --target agents-md --apply
sh $S --target skills-dir --dest <目录> --apply
```

## 运行时依赖（真正构建/部署时才需要）

| 依赖 | 用途 |
|---|---|
| `fnpack` | 构建 `.fpk` 包 |
| `appcenter-cli` | 安装/启动/卸载 FPK（fnOS 设备上） |
| `go` + `node` | 交叉编译与前端产物 |
| `python3` | 技能内脚本与文档自检 |
| `ssh` / `sshpass` | 远程部署与冒烟（按需） |

安装技能本身**不需要**这些；只在执行构建/部署步骤时才用得到。缺依赖时技能要求「如实报告缺失，不要伪造产物」。

## 自检与卸载

```bash
python3 -m unittest discover -s tests     # 若存在
```

卸载：删落地目录与对应提示词/规则文件；单文件规则删 `begin/end` 段落。

> 该目录自带 `.git`（浅克隆），安装器复制时自动排除 `.git/`，不会把仓库历史带进 Agent 工作区。

## Windows（PowerShell 5.1+）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -List
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Target claude-code -Apply
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Target skills-dir -Dest D:\skills -Apply
```

参数与 `install.sh` 一致：`-Target` `-Dest` `-Dep` `-Link` `-Apply` `-List`；默认 dry-run。
复制时同样排除 `.git/`、`.pushpack*`、`__pycache__/`、`*.pyc`、`node_modules/`。
