# BOSS 嵌入式岗位采集与投递

这是当前正式使用版本。它不需要 AI 或 API，不消耗模型 token，固定流程为：

```text
采集岗位数据 → 检查 CSV → 正式投递 / 中断后续投
```

## 只使用这三个入口

| 文件 | 做什么 | 会不会投递 |
| --- | --- | --- |
| `采集岗位数据.cmd` | 清理旧 CSV，按当前配置重新采集岗位 | 不会 |
| `START_BOSS_AUTO.cmd` | 使用已有 CSV 投递；有断点时自动续接 | 会 |
| `删除当前采集数据.cmd` | 删除当前采集的 CSV | 不会 |

根目录的旧“自定义”“重新采集并投递”“一键开始”等启动文件不属于当前正式流程，不要使用。

## 第一次使用

1. 双击 `安装依赖并登录.bat`。
2. 在程序打开的 BOSS 浏览器中手动登录。
3. 登录完成后回到黑色窗口按回车，让程序确认登录状态。
4. 双击 `采集岗位数据.cmd`。
5. 检查采集结果的职位、城市、公司和薪资。
6. 双击 `START_BOSS_AUTO.cmd` 开始正式投递。

采集结果目录：

```text
assets\post_data\embedded-robotics\
```

## 日常使用

### 重新采集

双击 `采集岗位数据.cmd`。它只采集，不会发送沟通；运行前会清理上一轮 CSV。

### 投递或第二天续投

双击 `START_BOSS_AUTO.cmd`。它只使用已有 CSV，不会重新采集。

今天关闭窗口、网络断开或 BOSS 出现当日次数提示后，第二天仍直接双击 `START_BOSS_AUTO.cmd`。程序会从最近一份未完成任务的下一个岗位继续。

投递断点在：

```text
assets\embedded_runs\<时间>_production\state\apply_checkpoint.json
```

### 只删除采集样本

双击 `删除当前采集数据.cmd`。它只删除当前采集 CSV，不会删除已沟通岗位账本或投递断点。

## 防止重复沟通

投递前会先用本地账本排除已经成功沟通的岗位链接。

进入 BOSS 聊天页后，脚本还会检查右侧是否已有**本人发出的任意一条消息或附件**。只要检测到一条，就判定为历史已沟通并跳过：不会输入新的招呼语、不会发送消息，也不会计入本轮新增投递。

## 改采集与筛选规则

只修改根目录的 `embedded_config.json`：

| 配置字段 | 作用 |
| --- | --- |
| `crawl_positions` | 搜索框中使用的一整串关键词；当前每个城市均输入“单片机，嵌入式，机器人，具身智能，FAE，硬件” |
| `production_cities` | 正式采集城市 |
| `match_keywords` | 岗位必须命中的方向词 |
| `exclude_keywords` / `blocked_title_keywords` | 屏蔽词 |
| `salary_min_k` / `salary_max_k` | 月薪范围；`null` 表示无上限 |
| `min_interval_seconds` / `max_interval_seconds` | 每个岗位之间的等待时间 |

改完配置后，先运行 `采集岗位数据.cmd`，检查 CSV，再运行 `START_BOSS_AUTO.cmd`。

## 重要目录

```text
embedded_auto.py                       采集、筛选、断点和投递总控
scripts\boss_crawler\                 BOSS 列表采集与城市校验
scripts\resume_matcher\auto_apply.py  聊天页检测与发送逻辑
assets\post_data\embedded-robotics\   当前采集样本
assets\embedded_runs\                 投递计划、日志、断点
assets\embedded_applied.jsonl          已成功沟通岗位账本
```

`README_自配置测试板.md` 是旧的自定义配置板说明；当前正式使用以本 README 为准。
