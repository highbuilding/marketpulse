# A 股板块/概念/题材数据源可行性验证

> 日期:2026-06-14  
> 结论:板块/概念/题材宇宙和成分股不应依赖盘中在线全量拉取。应预固化一份静态清单,盘中只更新行情指标;在线接口只做低频校准和手动刷新。

## 验证问题

AI 盘中决策助手的题材雷达原方案依赖:

- `stock_board_industry_name_em`
- `stock_board_concept_name_em`
- `stock_board_industry_cons_em`
- `stock_board_concept_cons_em`

这些接口来自东方财富 `push2.eastmoney.com/api/qt/clist/get`。需要先验证它们是否能承担 1-3 分钟级 collector 任务。

## 当前环境代理结论

本次验证已排除“代理导致失败”:

- 当前 shell 无 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。
- `akshare_worker.py` 在父进程未设置代理时会自动设置 `NO_PROXY="*"`。
- 直连复测使用:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    -u ALL_PROXY -u all_proxy NO_PROXY='*' no_proxy='*' ...
```

即强制禁用代理。

## 实测结果

### 东财板块/概念列表

强制直连下:

| 接口 | 结果 | 现象 |
|---|---|---|
| `stock_board_industry_name_em` | 失败 | 12s 超时;30s 复测最终约 74.5s 失败 |
| `stock_board_concept_name_em` | 失败 | 12s 超时;30s 复测最终约 73.2s 失败 |
| 东财行业列表直连 URL | 失败 | 约 6.1s `RemoteDisconnected` |
| 东财概念列表直连 URL | 失败 | 约 6.1s `RemoteDisconnected` |

错误类型为:

```text
ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

### 东财 push2 整体连通性

同样强制直连下,东财全 A 快照 `qt/clist/get` 也失败:

```text
spot_em_direct: RemoteDisconnected, elapsed ~= 6.1s
```

说明当前问题不是 AKShare 包装层,也不是 `17.push2` / `79.push2` 固定节点单点问题,而是本环境直连东财 `push2` 系接口不稳定。

### sina 对照

同样强制直连下,sina quote 正常:

```text
sina_quote_direct: ok, HTTP 200, elapsed ~= 0.19s
```

说明不是所有 A 股境内源不可用;问题集中在东财 `push2` 系。

## 设计影响

原方案中“每 1-3 分钟拉行业/概念列表,再对候选题材拉成分股”风险偏高:

- collector 冷启动可能拿不到题材宇宙。
- 盘中频繁拉东财/同花顺免费源会和其他 A 股任务抢限频与熔断预算。
- 板块/概念名称和成分股并非秒级变化数据,没必要高频在线拉。
- API/Web 更不能触发这些接口,否则用户刷新会放大数据源压力。

## 建议方案

采用“预固化宇宙 + 低频校准 + 盘中指标刷新”:

1. 预固化板块/概念/题材清单
   - 仓库内放一份版本化 seed,例如 `data/seeds/ashare_themes.json` 或 `core/themes/seeds/ashare_themes.json`。
   - 字段至少包含 `theme_code`、`theme_name`、`classification`、`source`、`members`、`seed_version`。

2. 预固化成分股
   - 每个板块/概念提前保存成分股列表。
   - 盘中角色识别使用实时 quote / bars / amount,不依赖在线成分股接口。

3. 在线接口只做低频校准
   - collector 可在收盘后或手动命令中尝试刷新东财/同花顺。
   - 刷新失败只记录 warning,继续使用上一版 seed。
   - 成功后生成 diff,人工确认或自动写入 `theme_universe` / `theme_memberships` 基表。

4. 盘中题材雷达只读静态宇宙
   - 候选题材从静态 universe + 实时行情指标计算。
   - `theme_snapshots` 保存每轮计算结果。
   - `theme_memberships` 保存角色和盘中指标,不是在线成分股事实源。

5. 数据源分级
   - S 级:本地 seed / SQLite 基表。
   - A 级:sina quote / 已有 bars / Redis quote cache。
   - B 级:东财/同花顺板块接口,仅用于低频校准。

## 落地口径

- `/market` 和 `/assistant` 不能触发 AKShare 板块接口。
- collector 盘中任务不能以“在线拉板块列表成功”为前置条件。
- 新题材发现不是盘中自动能力;先通过 seed 版本更新解决。
- 若未来东财直连稳定性恢复,也只提高校准成功率,不改变本地 seed 作为 SSoT 的架构。
