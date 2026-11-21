# dexterous-hand：机械手剪刀石头布（分发版）

人机石头剪刀布对局：摄像头识别手势 → 可插拔决策后端出拳 → MuJoCo / 真机机械手执行。

**许可**：项目代码 MIT（见 `LICENSE`）；`model/` 下第三方模型为 Apache-2.0（MuJoCo 模型见 `model/mujoco/LICENSE`，MediaPipe 模型来自 Google）。

## 分发版说明
这是从研发项目裁剪出的**可运行分发版**，只保留 7 个决策后端（见下），
去掉了实验性心理战/诊断后端与内部研发文档。原研发项目不在本仓库内。

### 决策后端（7 个，GUI「决策后端」下拉可选）
| 后端 | 依赖 | 说明 |
|---|---|---|
| 随机 | 无 | 纯随机 |
| 统计（克主频） | 无 | 出对手近 10 手主频的克制拳 |
| 融合 | 无 | 6 路预测器 + softmax 采样（Jev 路默认关） |
| Hedge7 | 无 | 7 专家集成（默认） |
| Jev | magpie 网关 key（`.magpie_key`） | 语义决策模型；无 key 时自动降级 |
| 外部 Agent | 常驻 Agent 进程经 `agent_commit.txt` 提交 | 无提交时自动降级 |
| 后端 A1 | 无 | 自适应方向：在线学人对带指向的话顺从/逆反 |

无外部依赖的后端（前 5 个 + A1）装完即跑；Jev / 外部 Agent 需额外配置。

## 结论（2026-11-18 实测）

三种手势都能做。用离线渲染逐张目视确认，关节角数据也吻合：
- rock：四指 MCP/PIP/DIP 全 ≈1.57 rad（弯曲握拢）
- paper：四指全 ≈0 rad（伸直），拇指张开
- scissors：食指+中指 ≈0（伸直），无名指+小指 ≈1.57（弯曲）

图在 `renders/rock.png`、`paper.png`、`scissors.png`。

## 模型来源

- 仿真模型：`github.com/Chestnut-Robotics/aero-open-sim`（`model/` 是 2026-11-18 clone 的 shallow copy）
- 场景 `model/mujoco/scene_right.xml`，手套 `right_hand.xml`（右手，7 执行器 / 16 关节）
- Apache-2.0（仿真模型），设计文件是 CC BY-NC-SA（注意商用边界）
- 主仓 `github.com/Chestnut-Robotics/aero-hand-open`（原 TetherIA），README 旧名仍在

## 控制映射（自己扫出来的，别凭猜）

关节角：0=伸直，1.57=弯曲。肌腱 ctrl **越大越伸直**。

| 执行器 | 索引 | 物理含义 | 弯曲/收拢 | 伸直/张开 |
|---|---|---|---|---|
| index/middle/ring/pinky A_tendon | 0-3 | 各手指一根肌腱 | 0.0585 | 0.1104 |
| thumb_cmc_abd | 4 | 拇指外展 | 0.30（收） | 1.70（张开） |
| th1_A_tendon | 5 | 拇指 CMC 屈 | 0.0262 | 0.0384 |
| th2_A_tendon | 6 | 拇指 MCP/IP | 0.0816 | 0.1121 |

三种手势 ctrl 向量（顺序即上表 0-6）：
```
rock:     [0.0585, 0.0585, 0.0585, 0.0585, 0.30, 0.0262, 0.0816]
paper:    [0.1104, 0.1104, 0.1104, 0.1104, 1.70, 0.0384, 0.1121]
scissors: [0.1104, 0.1104, 0.0585, 0.0585, 0.30, 0.0262, 0.0816]
```

## 让手出拳（两条命令，唯一入口）

```bash
cd D:/fs/workspace/projects/dexterous-hand

# 开一次常驻窗口（关窗口才退出）
.venv/Scripts/python.exe hand.py view          # 可带初始手势：view rock

# 之后随时换手势——只写 gesture.txt，0.1 秒退出，窗口实时变
.venv/Scripts/python.exe hand.py play rock
.venv/Scripts/python.exe hand.py play paper
.venv/Scripts/python.exe hand.py play scissors
```

`view` 常驻、`play` 换拳；手势定义只在 `gestures.py` 一处。
首次用先 `uv sync` 建 `.venv`（依赖锁在 `pyproject.toml`）。

### 真机执行（Aero Hand Open，SDK 按口，暂无实物）
`aero_driver.py` 读同一个 `gesture.txt`，把 RPS 手势翻译成 SDK 的 7 通道 actuation，
和 `hand.py` 仿真二选一（都监听 `gesture.txt`，互不干扰，对局流程不用改）。

```bash
# 没装 SDK / 没实物：自动 dry-run，只打印要发的 pose，不报错
.venv/Scripts/python.exe aero_driver.py --dry-run

# 有实物：装 SDK + 指定 COM 口（设备管理器查），先 Homing
python -m pip install aero-open-sdk
.venv/Scripts/python.exe aero_driver.py --port COM3
```

pose 取自 SDK 官方 examples（Open Palm / Peace Sign / Power Grasp），
**非官方 RPS 定义**；SDK 通道 0..6 = 拇指外展/屈曲/肌腱/食/中/无名/小指。
实机首次要跑 `python -m aero_open_sdk`（GUI）配电机 ID + Homing，再微调 pose。
SDK 官方测到 Python 3.10+，本项目 .venv 是 3.14，实机前先确认兼容。

## 人机对局（已跑通）：commit → reveal，GUI 按钮驱动

```bash
# 终端1：开机械手常驻窗口（只显示出拳，不镜像你的手）
.venv/Scripts/python.exe hand.py view paper

# 终端2：开对局（GUI 手动点「下一局」开一局，不自动循环）
.venv/Scripts/python.exe duel.py                        # 随机策略（默认）
.venv/Scripts/python.exe duel.py --seconds 3 --stable 6 # normal 模式倒计时/稳定阈值
.venv/Scripts/python.exe duel.py --strategy external     # LLM Agent 提交出拳
```

**三种模式（GUI 单选）**：
- `普通`：点「下一轮」→ T 秒倒计时 → 窗口内最后一个稳定手势为人承诺 → 揭晓
- `划两下`：点「下一轮」→ 先要求一次 no hand（开场把手拿出画面）→ 识别两次 ✊ 划拳
  （开场、两次之间、第二次之后都要 no hand 收手）→ 第三次出手为人承诺 → 揭晓。时间窗口动态到第三下。
- `按钮`：点「下一轮」→ 出拳按钮才出现 → 人点石头/剪刀/布当承诺（**不用摄像头，人不在镜头前也能玩**）→ 揭晓

**公平性与时序**：robot 决策在**识别/承诺结束后、揭晓前**锁定，不看人这一拳。
external 策略会**阻塞等 Agent 承诺**（窗口期间由常驻 Agent 写 `agent_commit.txt`），拿到后一次性消费；
绝不能在窗口开头就 decide——那样 external 读不到承诺会抛错、还会卡住摄像头/按钮。

GUI：内嵌 660px 摄像头预览、倒计时/划拳计数、双方出拳、裁判结果、比分；
每局结束停下，手动点「下一轮」才开下一局。按钮模式的出拳按钮进入对局才显示、揭晓后收起。

架构（决策可替换）：
- `recognize.py` 识别人 → `strategy.py` 决策 → `judge.py` 裁判计分 → `scoreboard.py` GUI → 写 `gesture.txt` 给机械手
- `strategy.py` 接口 `decide(strategy, context=None)`；内置 `random`（不看人）、`external`（读 Agent 提交）
- 加策略：写一个函数注册进 `STRATEGIES`，duel.py 不动
- 工作线程只通过 queue 和 GUI 通信；线程里抛异常只打 traceback、GUI 卡住——排查先看完整日志末尾

⚠️ 机械手出的是机器人的决策，不是镜像你的手。

**Jev 决策层（轮层快决策，已跑通）**：
- `jev_client.py`：经 magpie 网关调 TypeSafe Jev（System One 决策模型），~1.2s
  - 协议：`POST {gateway}/v1/systemone`，body `{model, state, questions:{id:{type,instructions,criteria}}}`
  - choice 返回 `{choice, confidence, probabilities}`；不生成文本
  - 模型池 `zensystemone`：`jev-1.13`（付费）/ `jev-1.13-free`（免费，cost 0）
  - 轮层用法：state=对手历史摘要 → 问「下一手最可能出什么」→ 出克它的拳
- 网关 key（**不入版本库**）：`magpie gateway-key issue --ttl 1h --name rps-jev --models "zensystemone/*" --from 242b0535f32f46e71240a241 --json`，把 key 写进 `.magpie_key`
- 策略注册在 `strategy.py`：`random` / `stats`（纯统计主频克制拳）/ `jev`（Jev+置信度门限）/ `external`（MCP 注入）
- **级联**：Jev confidence < 0.55 时退回 stats（它自己不确定时别硬用）

用 Jev 出拳：`.venv/Scripts/python.exe duel.py --strategy jev`

⚠️ 踩过的坑（别再犯）：
- **CLI 不能自己多起一个网关**：magpie GUI 已在跑时，再执行 `magpie-cli.exe` 会抢 3430 端口 → 之后所有 CLI 调用刷插件错误并超时（实测 60s 不到命令）。判据：`netstat -ano | grep :3430` 看 LISTENING 是谁；多出来的 magpie-windows-amd64 进程要杀掉
- **模型 id 认准池**：`zensystemone/jev-1.13-free` ≠ `opencode-zen-free/jev-1.13-free`（两个池都有 jev，名字相近）。用 `magpie provider models zensystemone` 读真 id
- `magpie provider test zensystemone` 会走 decide 通道实测决策模型（实测 `✓ decide 1253 ms · jev-1.13`）
- commandcode-plan 池的 `typesafe/jev` **走不通**：Go 套餐无 API 权限（403 官方文案「Your Go plan doesn't include API access」）

**LLM Agent 直调出拳接口**（external 策略）：
```bash
.venv/Scripts/python.exe hand.py agent rock   # Agent 提交本回合承诺，揭晓时消费（一次性）
```
**外部 Agent 提交（external 后端用）**：
- `hand.py agent <move>` 写 `agent_commit.txt`（承诺，不立刻亮），和 `hand.py play`（直接改机械手当前手势）分开。
- 文件契约（不依赖 GUI 进程存活）：
  - `pending_decision.json`：点「下一轮」时落盘 `{user_id, 三层快照, ts, round_hint}`
  - `history_snapshot.json`：每轮揭晓后落盘三层快照
  - `agent_commit.txt`：Agent 写入出拳，external 策略揭晓时一次性消费
- 分发版不含 MCP server；任何常驻 Agent 只要按上面写文件就能接 external 后端。

**用户与三层历史**（已跑通）：
- `auth.py`：本地注册/登录，密码 PBKDF2+随机盐，绝不存明文；GUI 顶部登录区，登录后才能点「下一轮」
- 层级：**局 > 轮**，固定 5 轮一局（`--rounds-per-bout` 可调），局内胜场多者赢
- `history.py` 三层：轮（每次出拳裁决，带 user_id）→ 局（5 轮一组，各用户独立）→ 画像（各拳频率/胜率/输后换拳率/局首偏好）
- 多用户隔离 + 全局画像：`profile(user)` 单人、`global_profile()` 全体
- 每轮落盘 `history_snapshot.json`（含 user_id + 该用户轮/局/画像 + 全局画像），供外部 Agent 读取

实测：testuser 登录打 11 轮，日志 `[testuser] 局1 轮1` → `局2 轮6`（第5轮封局）→ `局3 轮11`，分组正确。

坑：线程里抛异常只会打印 traceback、GUI 卡住不推进——排查先看完整日志末尾，别猜摄像头/线程。

## 摄像头识别手势（已跑通）

```bash
.venv/Scripts/python.exe recognize.py           # 实时窗口，q 退出，终端打印识别结果
.venv/Scripts/python.exe recognize.py --once    # 拍一张识别，存 recognize_once.jpg
```

- 摄像头：USB Realtek，index 0（640×480，脚本里 0-3 探测前已确认）
- 模型：`model/hand_landmarker.task`（MediaPipe HandLandmarker，float16，7.5M）
- MediaPipe 1.2.3，Python 3.14 可装（实测）
- 判定法：21 关键点数伸直手指——rock=0 根、scissors=食中伸其余弯、paper=4+ 根
- 实测三种手势都对；换手途中会有过渡帧跳变，稳定后正确
- 注意：OpenCV `imwrite` 吃不了中文路径，输出文件名都用英文

## 模型/控制细节（备查）

- 环境：Windows，mujoco 4.0.1（uv 现装），numpy、pillow
- 坑：`Renderer` 默认 framebuffer 640×480，超过会报错。当前用 480×480 + 自由相机（az=90, el=75, lookat=[0.15,0,0], dist=0.42）——side 相机太平看不清，自由相机正对掌面才辨得出手势。
- 从 home 关键帧起步（`data.qpos[:]=home.qpos`），set ctrl 后 `mj_step` 800 步收敛。
- `view.py` 是早期相机查看脚本，CLI 不依赖它。

## 未完成 / 下一步（等阿哲定方向）

- [x] 摄像头识别人的手势（MediaPipe HandLandmarker，usb-camera index 0）
- [x] 识别→决策→机械手出拳（commit→reveal 两段式，公平）
- [x] 随机决策 + 裁判 + 计分 + GUI 倒计时/结果
- [x] LLM Agent 直调接口（`hand.py agent <move>` → external 策略消费）
- [ ] 换更强的决策策略（读历史分布 / LLM 现场推理，注册进 strategy.py）
- [x] 可插拔决策后端（random/stats/fusion/hedge/jev/external/后端A1）

## 小霁来啦

11月21号给Jiya Vieuna建了独立的GitHub账号，并添加为本仓库的协作者。
