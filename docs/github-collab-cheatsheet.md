# GitHub 协作速查表

给团队成员用。覆盖单人到多人协作的完整命令 + 概念。

## 核心心智模型

1. **commit = 改动记录**，不是整份文件；合并 = 把改动应用到目标分支
2. **分支 = 独立改动线**，不碰主分支
3. **三套身份互相独立**：浏览器登录态 ≠ gh active ≠ git 凭证
4. **fork PR 方向**：head = 你的 fork，base = 源仓库（最容易选错）
5. **pull = fetch + merge**，同步不是覆盖
6. **remote** 是远端别名的类别；`origin`（你 clone 的仓库）、`upstream`（源仓库）都是 remote 实例

## active 账号 与 git 凭证

- `gh auth switch` 只改 gh 工具的默认账号（active）
- `gh auth setup-git` 把当前 active 账号的 token 写进 git 凭证配置
- 两者不会自动同步，所以要先 switch（选谁）再 setup-git（同步给 git）

## 一、Git 本地基础

```bash
git status                          # 当前状态：分支、改动
git branch                          # 列本地分支（* = 当前）
git branch --show-current           # 只看当前分支名
git log --oneline -3                # 最近 3 条 commit
git checkout -b <分支名>            # 新建并切到分支
git checkout <分支名>               # 切换分支
git diff --stat                     # 改动文件/行数统计
git diff                            # 具体改动内容
```

## 二、提交

```bash
git add <文件>                      # 加入暂存区
git add -A                          # 所有改动（含删除/新增）
git commit -m "说明"               # 打包成一个 commit

# commit 报 Author identity unknown 时，只在本仓库配（不用 --global）：
git config user.name "<用户名>"
git config user.email "<用户名>@users.noreply.github.com"
```

## 三、远端

```bash
git clone <URL>                     # 克隆远端仓库到本地
git push origin <分支>             # 推本地分支到 origin
git push -u origin <分支>          # 推并建立追踪（以后直接 git push）
git pull origin main                # 拉远端 main 并合并（= fetch + merge）
git remote -v                       # 看所有 remote 地址
git remote add upstream <URL>       # 加"上游"remote（fork 后指源仓库）
git remote get-url origin           # 看 origin URL
git remote set-url origin <URL>     # 改 origin URL
```

## 四、fork 同步（场景④）

```bash
git fetch upstream                  # 下载上游改动（不改工作区）
git merge upstream/main             # 合入当前分支
git rev-list --left-right --count origin/main...upstream/main
                                    # 左=你 fork 独有，右=上游独有；"0 0"=一致
```

## 五、GitHub CLI（gh）

```bash
# 账号
gh auth status                      # 看已登录账号 / 谁是 active
gh auth login                       # 登录
gh auth switch --user <账号>       # 切 active
gh auth token --user <账号>        # 取 token（勿外传）
gh auth setup-git                   # 把 active 凭证配给 git

# 仓库
gh repo create <owner>/<名> --public --description "..."
gh repo fork <owner>/<repo> --clone=false
gh repo view <owner>/<repo>

# PR
gh pr create --repo <owner>/<repo> --head <谁>:<分支> --base <分支> \
  --title "..." --body "..."
gh pr view <号> -R <owner>/<repo> --json 字段 --jq '...'
gh pr diff <号> -R <owner>/<repo>
gh pr review <号> -R <owner>/<repo> --approve --body "..."
gh pr merge <号> -R <owner>/<repo> --merge     # 也可 --squash / --rebase

# API（查分支/commit/文件/collaborator/PR 的 head-base）
gh api repos/<owner>/<repo>/...
```

## 六、四种协作场景

| 场景 | 写权限 | 改在哪 | 合并 |
|---|---|---|---|
| ① 同仓库分支 | 有 | 原仓库开分支 | PR → 合并 |
| ② Fork+PR | 无 | 自己 fork 的分支 | PR 回源仓库 → 主人合并 |
| ③ 直接推 | 有 | 主分支（团队应避免） | 无 PR、无 review |
| ④ 跨 fork 同步 | 无 | 自己 fork | fetch upstream + merge |

## 七、常见坑

- **不能 fork 自己的仓库**：源仓库和 Fork 账号不能是同一个
- **Author identity unknown**：git 和 GitHub 登录独立，git 要单独配署名
- **push permission denied**：浏览器登录态 / gh active / git 凭证三套独立
- **不能批准自己的 PR**：作者本人不能 approve，必须另一个人
- **fork PR base 选错**：默认可能是自己的 fork，要手动改成源仓库
- **fork 落后上游**：提新 PR 前先同步，避免基于旧代码改导致冲突
- **setup-git 无 --user**：先 `gh auth switch --user X`，再 `gh auth setup-git`
