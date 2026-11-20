# 岚桥耐久试验室固定订阅采集站

白班值班员用试验规程、流能力目录和设备录制开轮，巡查有效值、超限和坏帧。停机后签发报告。夜班质检员可以在源端关闭后按成果号检查原码、极值出处和当时的定标口径。

需要 Python 3.11 和标准库中的 SQLite。在物化后的项目根目录运行完整回归。

```sh
python3 -B check_project.py
```

运行一班样例。样例在临时目录签报，然后关闭归档连接并在没有采集对象的领取入口重开。

```sh
python3 -B demo.py
```

## Python 接口

1. 用 `station.procedure.registration.ProcedureRegistry.register(rates, masks, calibrations, capacity)` 登记规程。`rates` 是流名到正整数保留速率的映射，`masks` 是掩码名到流名集合的映射，`calibrations` 为每流提供 `CalibrationSpec(scale, offset, upper_limit)`。`get_draft` 返回当前草案的只读视图。`revise(procedure_id, rates, masks, calibrations, capacity)` 更新草案。非法输入报 `ValueError`，未知规程报 `KeyError`。
2. 用 `station.capture.recording.RecordingCatalog.register(recording_id, recording)` 登记录制。`Recording` 接收原码下限、上限及一组 `SourceFrame(source_seq, stream, raw_code)`。源序是严格递增的整数，可以有间隙。原码越界的录制仍可回放，由实收裁定。`Recording.from_document` 接受样例中的录制对象。
3. 用 `station.capture.session.CaptureStation(procedures, recordings)` 创建站端。调用 `open_round(procedure_id, subscription, slots, recording_id)`。订阅包含至少一条已登记流。`slots` 有两个位置，每个位置可为 `None` 或目录掩码名。掩码并集须覆盖订阅，并按每流一次合计保留速率。等于额度可开轮。配置非法报 `AdmissionError`，未知引用报 `KeyError`。每个成功轮独立冻结规程并持有录制游标。
4. `step(round_id)` 消费一条录制，返回物理观察、实收裁定及 `accepted`、`filtered` 或 `rejected` 状态。`inject_source(round_id, frame)` 可在两次串行推进间供帧，源序须位于已产生帧之后、下一条录制之前。录制耗尽时报 `RecordingExhausted`。`redeliver(round_id, frame)` 在实收侧处理已有源序或更老的回投，保留同序首件，冲突或倒序留异常。`observations`、`receipts` 和 `recording_position` 分别查询物理观察、实收账和录制游标。
5. `live(round_id)` 返回独立的按流视图。微单位值等于比例乘原码加偏移。严格大于上限才计超限。两个等值极值的出处取最早源序。空流的有效数、和及超限为零，极值和出处为空。坏帧单独累计。未知轮报 `UnknownRound`。
6. `stop(round_id)` 固定本轮证据。已停轮的推进、供帧和回投报 `RoundStateError`。用 `station.archive.publication.ArchivePublisher(station, store).seal(round_id)` 签发。运行轮拒绝签发。SQLite 提交失败报 `CommitError`，可以从原停轮重试。成功后同轮重投复领原成果号和回执。
7. 用 `station.archive.storage.ArchiveStore(path)` 打开归档。重复 `initialize()` 保留现有数据。`close()` 关闭连接。归档将报告、逐样证据和回执一次提交。业务整数以十六进制字符串精确保存，Python 领取接口恢复为整数。草案、录制和运行轮在进程内持有，已封报告可以独立重开领取。
8. 用 `station.archive.lookup.ArchiveReader(store).read(result_id)` 领取。返回固定报告、原源序、原码、裁定、冻结口径、回执及复算统计。未知成果报 `UnknownResult`。已知成果的同次记录缺失、裁定矛盾或复算不符报 `EvidenceMismatch`。
9. 不停流换采用 `station.capture.session.CaptureStation.switch_subscription(round_id, subscription, slots, *, after_action=None)`。终态按开轮时冻结的规程与额度校验，非法终态报 `AdmissionError`；未知轮报 `UnknownRound`，已停轮报 `RoundStateError`。每轮至多成功一次，成功后再请求报 `RoundStateError`。终态合法但无合法动作路径时返回 `None`，不写槽、不标切点、不调回调，旧订阅与账目原样保留，可继续供帧或另申请换采。成功返回 `SwitchRecord`，含 `subscription`、`final_slots`、`cut_after_source_seq` 与按执行顺序排列的 `actions`。每个 `SwitchAction` 有 `slot`（`0`/`1`，独立切点为 `None`）、`mask`（改入掩码，清槽或独立切点为 `None`）、`cutover` 与 `after_source_seq`。`after_action(action)` 在每个实际动作后调用且不抛异常；其中可用 `inject_source` 按实际滤表供帧。换采轮 `live` 返回当前段的逐流账；`stop` 的 `tallies` 为新段计量、`before_tallies` 为旧段计量，未换采轮 `before_tallies` 为 `None`。报告与证据一次提交，领取时 `evidence.switch` 给出终态、切点与动作，`report.before_tallies` 与 `audit.before_tallies` 给出旧段复算。

## 手算样例

两槽为 AB 和 A，额度为 2。序号 10、14 的 A 原码均为 5，按 2x+1 得到两个 11，和为 22，极值出处均为 10。上限是 11，超限数为零。序号 15 的原码 11 超出设备原码上界 10，计一件坏帧。序号 11 的 C 被设备过滤。B 已订阅且本轮无实收。关源领取保留 10、14、15 三个实收首件的原码及原源序间隙。
