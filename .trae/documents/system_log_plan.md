# 系统日志功能 - 实施计划

## [ ] 任务 1: 创建 log 目录
- **Priority**: P0
- **Depends On**: None
- **Description**: 
  - 在 main.py 同目录下创建 log 目录用于存储游戏系统日志
  - 确保目录存在，不存在则自动创建
- **Success Criteria**:
  - log 目录在正确位置创建
- **Test Requirements**:
  - `programmatic` TR-1.1: 目录 `/Users/ryou/Documents/code/blood_on_the_clocktower_K-main/log` 存在
- **Notes**: 使用 Python 的 os.makedirs 函数，设置 exist_ok=True

---

## [ ] 任务 2: 在 Game 类初始化时创建日志文件
- **Priority**: P0
- **Depends On**: 任务 1
- **Description**: 
  - 在 Game 类 __init__ 中创建以 game_id 为文件名的日志文件
  - 文件位置：`log/{game_id}.log`
  - 记录游戏开始信息和初始状态
- **Success Criteria**:
  - 游戏创建后，log 目录下生成对应 game_id 的日志文件
  - 日志文件包含游戏初始信息（创建时间、剧本、玩家数量等）
- **Test Requirements**:
  - `programmatic` TR-2.1: 创建游戏后，`log/{game_id}.log` 文件存在
  - `programmatic` TR-2.2: 日志文件包含游戏创建时间、剧本ID等初始信息
- **Notes**: 在 Game 类 __init__ 方法中添加日志文件初始化代码

---

## [ ] 任务 3: 添加系统日志记录方法
- **Priority**: P0
- **Depends On**: 任务 2
- **Description**: 
  - 添加 `add_system_log()` 方法用于记录系统级日志
  - 该方法同时写入文件和内存中的 game_log 列表
  - 保持现有的 add_log() 不变，不影响展示给玩家的日志
- **Success Criteria**:
  - add_system_log() 方法能正确写入文件
  - 系统日志格式一致，包含时间戳、类型和消息
- **Test Requirements**:
  - `programmatic` TR-3.1: 调用 add_system_log() 后，日志文件中有对应记录
  - `programmatic` TR-3.2: add_log() 方法不受影响，继续正常工作
- **Notes**: 系统日志格式建议：`[YYYY-MM-DD HH:MM:SS] [TYPE] Message`

---

## [ ] 任务 4: 在关键操作节点添加系统日志
- **Priority**: P0
- **Depends On**: 任务 3
- **Description**: 
  - 在玩家产生操作的关键节点添加系统日志记录
  - 重点节点包括：
    - 游戏创建和角色分配
    - 夜晚开始/结束
    - 白天开始/结束
    - 玩家使用技能（投毒、保护、击杀等）
    - 玩家获取信息
    - 提名和投票
    - 处决
    - 游戏结束
- **Success Criteria**:
  - 每个玩家操作都有对应的系统日志记录
  - 日志详细描述玩家操作，包含角色名、目标、结果等信息
- **Test Requirements**:
  - `programmatic` TR-4.1: 夜晚技能使用后，日志文件有对应记录
  - `programmatic` TR-4.2: 白天提名投票后，日志文件有对应记录
  - `human-judgement` TR-4.3: 日志描述清晰，包含完整的操作信息
- **Notes**: 使用用户要求的模板格式，如：
  - "第N天夜晚，玩家X（角色Y）对玩家Z（角色W）使用技能，得到信息/结果"

---

## [ ] 任务 5: 测试和验证
- **Priority**: P1
- **Depends On**: 任务 4
- **Description**: 
  - 运行现有测试，确保不破坏现有功能
  - 手动测试一个完整游戏流程，验证日志记录完整性
  - 检查日志文件格式和内容是否符合要求
- **Success Criteria**:
  - 所有现有测试通过
  - 完整游戏流程的日志记录完整且正确
- **Test Requirements**:
  - `programmatic` TR-5.1: 运行 `python3 -m pytest tests/` 所有测试通过
  - `human-judgement` TR-5.2: 日志文件内容完整，覆盖所有关键操作节点
