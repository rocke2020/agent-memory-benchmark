# LongMemEval gold-answer case studies

> **TL;DR:** For question `6d550036`, the dataset's gold answer of `2` is defensible only under a narrow "lead a team" interpretation; the more natural "lead or own a project" interpretation supports **3**, and the declared `answer_session_ids` are incomplete and noisy because they omit the strongest current team-lead session. This ambiguity does not change the official-versus-local score gap because both runs failed the case.

## Terms

These terms keep the answer value, its supporting sessions, and the interpretation of leadership separate.

- **Gold answer**: the expected answer used by the benchmark judge; here it is `2`.
- **Gold evidence**: the raw sessions named by `answer_session_ids` as support for the gold answer.
- **Team-leading interpretation**: count a project only when the user leads people working on it.
- **Project-leading interpretation**: count a project when the user owns or directs it, including a solo project.

## 1. Case 1 verdict: `6d550036`

The recommended answer is **3** under ordinary project-leading semantics, while **2** remains an acceptable qualified answer only under the narrower team-leading definition.

| Field | Value |
|---|---|
| Question | How many projects have I led or am currently leading? |
| Question date | 2023/05/30 (Tue) 23:32 |
| Dataset gold | `2` |
| Narrow team-leading count | `2`: Marketing Research and June product-feature launch |
| Recommended project-leading count | `3`: add the solo Data Mining/customer-data project |
| Case classification | Gold-answer and gold-evidence ambiguity |

The number `2` is therefore not intrinsically impossible, but it is not self-explanatory: its defensible rationale requires a session omitted from the declared gold evidence, while the natural project-ownership reading produces `3`.

## 2. Source and review method

The review used the immutable raw S-split history, inspected all 47 sessions as one question-scoped user history, and treated assistant paraphrases as secondary rather than as new evidence about the user.

- Source: `datasets/longmemeval-cleaned/longmemeval_s_cleaned.json` in the parent evaluation workspace.
- SHA-256: `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`.
- Selection: `question_id == "6d550036"`.
- Counting rule: identify a distinct project, establish personal leadership, then deduplicate descriptions of the same project.
- Guardrail: causal wording such as `led to a significant increase` is not evidence that the user led a project.

The May 21 customer-data project and the May 29 solo Data Mining project both analyze customer purchase data for patterns and trends, so this study treats them as one ongoing project rather than two.

## 3. Raw-history evidence

Three projects have sufficient support under project-leading semantics; the remaining candidates show completion, planning, participation, or collective activity without establishing personal project leadership.

| Candidate | Session | Raw user evidence | Judgment |
|---|---|---|---|
| Marketing Research class project | `answer_ec904b3c_1` | "I led the data analysis team" | **Count.** Explicit team leadership inside the project. |
| June product-feature launch | `2e4430d8_2` | "leading a team of five engineers"; "assign tasks" | **Count.** The user leads the team, sets the project plan, and assigns its work. |
| Solo Data Mining/customer-data project | `answer_ec904b3c_2` | "working on a solo project" | **Count under project-leading semantics.** The user is the sole project owner; exclude only under the narrow team-leading definition. |
| High-priority project | `2e4430d8_2` | "project I completed two months ahead of time" | **Do not count.** Completion is not leadership, and `led to` later in the sentence is causal. |
| Nigeria running-water project | `sharegpt_J7ZAFLd_0` | "I am planning a project in Nigeria" | **Do not count without inference.** Planning does not establish leadership. |
| Case competition | `answer_ec904b3c_4` | "I recently participated" | **Do not count.** Participation is not leadership. |
| Influencer research | `answer_ec904b3c_3` | "presented a poster on my research" | **Do not count.** Authorship and presentation do not establish project leadership. |
| Ethereum Mexico event/grant project | `sharegpt_zciCXP1_12` | "we, as the Ethereum México Community Team" | **Do not count without inference.** Collective `we` does not establish the user's individual role. |
| Three prior ESP events | `sharegpt_zciCXP1_12` | "we hosted 3 events" | **Do not count.** Collective hosting neither proves individual leadership nor that each event is a project. |
| Wood-and-stone next project | `a9981dc6_3` | "combining wood and stone in my next project" | **Do not count.** This is a future idea, not a led or active project. |

The high-priority item is the clearest false positive in the generated answers: `two months ahead of time` measures schedule variance, not when the project occurred, and `which led to` means "caused."

## 4. `answer_session_ids` defect

For this case, `answer_session_ids` do not form a complete or internally consistent evidence map for either defensible interpretation of the answer.

The raw declaration is:

```text
answer_ec904b3c_1
answer_ec904b3c_4
answer_ec904b3c_3
answer_ec904b3c_2
```

Its problems are:

- It omits `2e4430d8_2`, the strongest evidence that the user currently leads a team on the June product-feature project.
- `answer_ec904b3c_4` establishes participation in a case competition, not leadership.
- `answer_ec904b3c_3` establishes research authorship and presentation, not leadership.
- `answer_ec904b3c_2` supports the count only under project-leading semantics, which produces `3` once the omitted June project is included.

A coherent evidence set for the narrow answer `2` would be `answer_ec904b3c_1` plus `2e4430d8_2`. A coherent evidence set for the recommended answer `3` would add `answer_ec904b3c_2`; the current-customer-data statement inside `answer_ec904b3c_1` corroborates that solo project but should not create a fourth count.

## 5. Independent judgment

An independent subagent, started without the conversation's conclusions and instructed to read only the raw 47-session history, also selected **3** as the most natural answer.

The independent review agreed on four points:

1. Marketing Research, the June product-feature launch, and the solo Data Mining project are the three supported projects under project-leading semantics.
2. High-priority completion and causal `led to` do not prove leadership.
3. Nigeria planning and collective Ethereum activity lack enough evidence for personal leadership.
4. The declared `answer_session_ids` are incomplete and noisy, with `2e4430d8_2` as the material omission.

This second judgment is a semantic review, not a benchmark rerun or a claim that one model's opinion can redefine the dataset. Its value is that an isolated reader reached the same evidence boundary from the raw history.

## 6. Benchmark impact and adjudication

This case is annotation-confounded rather than a clean memory-system error: both the official and local runs failed against the same gold, so it belongs to the 13-question both-fail group and contributes nothing to the 25-verdict official-versus-local gap.

For published-score reproduction, retain the dataset's original `2` so results remain comparable. For analysis or a future in-house benchmark, use this adjudication policy:

- Prefer `3` when `lead a project` includes sole project ownership.
- Accept `2` only when the answer explicitly applies the team-leading interpretation and names Marketing Research plus the June product-feature project.
- Reject answers that count the high-priority project as led, confuse causal `led to` with leadership, or promote planning and collective `we` to personal leadership without qualification.
- Report the case as `gold-ambiguous`; do not attribute its failure solely to retrieval, answer-model quality, or judge quality.

Related analysis: [LongMemEval Hindsight DeepSeek reproduction summary](20260-0822/summary.md).
