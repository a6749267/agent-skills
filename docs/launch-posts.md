# agent-skills — launch posts

Repo: https://github.com/a6749267/agent-skills
Skills directory: https://skills.sh/a6749267/agent-skills

---

## Post 1 — Reddit (r/ClaudeAI, r/ClaudeCode, r/programming)

**Title options:**
- I built a skill that screen-records your real app as a tutorial video — with zero real data leaking on screen
- Record your app as a tutorial video without exposing real customer data (open source, framework-agnostic)
- 4 bugs that make every AI-recorded product demo look fake — and the checks that catch them

**Body:**

Recording a demo of your app for tutorial/marketing videos is annoying for one reason: the
moment you log in with real data, you have a leak waiting to happen. So I built a skill that
records the **real** running app — real UI, real interactions — while every value on screen is
synthesized.

```bash
npx skills add a6749267/agent-skills
```

It works with Claude Code, Cursor, Codex, Cline, Amp and ~20 more agents (anything that reads
`SKILL.md`). The repo is framework-agnostic on purpose — there's a generic pipeline plus a
thin **adapter** per app, so porting it to your stack is a config file plus one Python file.

Four failure modes that make AI-generated demos look fake, all now caught by automated checks:

1. **QA ran against a different script, not the final cut.** The verifier now runs on the
   rendered file. This is the single most common reason people say "this is just a marketing
   homepage screenshot".
2. **A brand-new account is naturally empty**, so half the panels render "no data yet" and it
   looks like a hollow shell. The synthetic-data generator fills every module.
3. **The backend hardcodes a summary field to 0**, so the header says `+0.00` while the rows
   sum to `-1200.00`. Numbers are now reconciled — header total = sum of line items = card
   values, asserted automatically.
4. **The model reads the digits**, so it reads `-1200.00` as `-1400.00` and then confidently
   claims your correct data is inconsistent. Numbers are now compared arithmetically, not by
   reading pixels.

Also included: burned-in subtitles with forced alignment to the audio, BGM matching with
ducking so voice stays audible, a dead-frame check (a recording that sits still for 20s loses
the viewer), and a leak scanner that runs over the *delivered* file before it ships.

Everything is MIT licensed. The 40+ failure modes I hit are documented as a pitfalls reference
indexed by symptom, so you can look up "why does my subtitle drift" instead of debugging blind.

If you've tried to get an agent to record a real app demo and given up, this might save you a
weekend. Feedback and PRs very welcome — especially adapters for other stacks.

Repo: https://github.com/a6749267/agent-skills

---

## Post 2 — Hacker News

**Title:** Show HN: agent-skills – record a real app as a tutorial video with zero real data on screen

**Body:**

I built two agent skills (Claude Code / Cursor / Codex compatible, ~20 agents) that turn a
running web app into a tutorial video — while keeping every on-screen value synthetic.

```bash
npx skills add a6749267/agent-skills
```

https://github.com/a6749267/agent-skills

The interesting part isn't the recording, it's the four ways these demos normally look fake,
each of which now has an automated check:

- QA scripts that validated the wrong artifact instead of the final video
- Empty brand-new accounts making half the UI render "no data"
- Summary fields hardcoded to 0 while row items don't sum to them
- A vision model misreading `-1200.00` as `-1400.00` and then rejecting your correct data

Numbers are now reconciled arithmetically (header total = sum of rows = card values), the
verifier runs against the rendered file, and a leak scanner runs over the deliverable before
shipping.

Design notes:
- **Framework-agnostic**: generic pipeline + one adapter file per app. No assumptions about
  your framework, no hardcoded paths (not even `Path.home()`).
- **Subtitles are forced to align to the audio**, because drifted subtitles are the most
  common reason a tutorial gets closed.
- **BGM is ducked against the voice** so music never buries narration.
- A **dead-frame check** catches a recording that sits still for 20 seconds.

The 40+ real failure modes are documented in a pitfalls reference indexed by symptom. MIT
licensed. Adapters for other stacks are the most welcome contribution.

---

## Post 3 — 掘金 / 知乎 / 中文技术社区

**标题：** 用 AI Agent 把真实 App 录成教程视频，屏幕上一个真实数据都不漏

**正文：**

录产品教程视频最烦的不是录屏，是**登录真账号那一刻**——只要屏幕上有真名、真单号、真金额，
视频就不能发。我做了两个 skill：录真实运行的 web app，屏幕里的所有数字都是合成的。

```bash
npx skills add a6749267/agent-skills
```

Claude Code、Cursor、Codex、Cline 等 20+ 个 agent 都能用（凡是认 `SKILL.md` 的都行）。
仓库本身**框架无关**：通用管线 + 每个 app 一个 adapter，换栈就是改一个配置文件加一个 Python 文件。

**为什么录出来的 demo 总是"一眼假"？** 我踩了 40 多个坑，其中 4 个是致命的，现在都做成了自动检查：

1. **验收跑的不是成片** —— 验收脚本验的是中间产物，最后观众看到的是"就是个首页截图"。现在验收直接针对渲染出来的成片。
2. **新账号天然是空的** —— 10 个模块 6 个显示"暂无"，一眼空壳。现在合成数据把每个模块都填满。
3. **后端把汇总字段写死 0** —— 顶栏 +0.00，明细却是 -1200.00，一眼穿帮。现在数字自洽：顶栏汇总 = 明细之和 = 卡片数值，自动断言。
4. **让模型去"读"数字** —— 模型把 -1200.00 读成 -1400.00，然后反过来质疑你本来正确的数据。现在数字用算术校验，不靠读像素。

另外还内置了：字幕强制对齐音频（字幕飘是最常见的弃播原因）、BGM 智能选段 + 人声优先的 ducking 混音、
死画面体检（录屏静止 20 秒观众就划走）、以及交付前对成片跑的脱敏扫描。

40 多个坑按症状做成了 A–H 八组的踩坑手册，可以直接当症状词典查。MIT 协议。

最欢迎的贡献是**其他技术栈的 adapter**。

仓库：https://github.com/a6749267/agent-skills

---

## Post 4 — X / Twitter (thread)

1/ Record your real app as a tutorial video — with zero real customer data on screen.
I built an open-source agent skill for it. 🧵

2/ The hard part isn't recording. It's that AI-generated demos look fake, and there are 4
reasons. Each one now has an automated check. 🧵

3/ 1. QA validated the wrong artifact — not the final video. So users watched a "product
tour" and said "this is just your marketing homepage". Now the verifier runs on the rendered
file.

4/ 2. A fresh account is empty, so half the UI shows "no data" and it reads as a hollow
shell. Now a synthetic-data generator fills every module.

5/ 3. The backend hardcodes the summary field to 0 → header says +0.00 while rows sum to
-1200.00. Numbers are now reconciled arithmetically.

6/ 4. The model reads -1200.00 as -1400.00, then confidently argues your correct data is
wrong. Now we compare numbers, not pixels.

7/ Also in: subtitles forced to align to audio, BGM ducked so voice stays audible, and a
dead-frame check (a recording that sits still for 20s loses the viewer).

8/ Install:
npx skills add a6749267/agent-skills

Works with Claude Code, Cursor, Codex, Cline + ~20 more. Framework-agnostic — one config file
plus one adapter per app.

9/ MIT licensed. The 40+ failure modes are documented, indexed by symptom. Adapters for other
stacks are the most welcome contribution 👇

github.com/a6749267/agent-skills
