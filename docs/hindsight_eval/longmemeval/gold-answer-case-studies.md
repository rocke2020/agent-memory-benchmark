# LongMemEval gold-answer case studies

> **TL;DR:** Question `6d550036` has an ambiguous project count: `2` is defensible only under a narrow team-leading interpretation, while ordinary project-leading semantics support **3**, and its declared evidence sessions are incomplete and noisy. Question `51a45a95` most likely intends **Target**, but the user never directly says the coupon was redeemed there; its evidence session is correct, while its turn-level `has_answer` annotation omits the Target-bearing turn needed to answer where.

## Terms

These terms distinguish an answer value, its supporting sessions and turns, and any interpretation needed to connect them.

- **Gold answer**: the expected answer used by the benchmark judge.
- **Gold evidence**: the raw sessions named by `answer_session_ids` as support for the gold answer.
- **Turn-level evidence**: turns marked with `has_answer: true` for turn-level retrieval evaluation.
- **Contextually inferred gold**: an answer implied by nearby turns but not directly stated in the turn describing the queried event.
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

## 7. Case 2 verdict: `51a45a95`

The dataset's answer **Target** is the intended and most plausible session-level answer, but it is not directly entailed by a user statement. This case should remain unchanged for official-score reproduction and be classified as contextually inferred or weakly grounded in annotation-quality analysis.

| Field | Value |
|---|---|
| Question | Where did I redeem a $5 coupon on coffee creamer? |
| Question date | 2023/05/30 (Tue) 20:42 |
| Question type | `single-session-user` |
| Dataset gold | `Target` |
| Declared gold evidence | `answer_d61669c7` |
| Recommended adjudication | Retain `Target` for reproduction; flag as weakly grounded for data-quality analysis |
| Case classification | Contextually inferred gold and incomplete turn-level evidence |

The user discusses Target immediately before and after describing the coupon redemption, so Target is the natural conversational resolution. However, the user never says "I redeemed the coupon at Target," leaving another retailer logically possible.

## 8. Source and review method

The review scanned all 50 sessions in this question's immutable raw S-split history, then reconstructed the declared evidence session in turn order. Exact anchor checks found every relevant user mention in `answer_d61669c7`, and the human-readable polished record matched the raw record after its session wrappers were removed.

- Source: `datasets/longmemeval-cleaned/longmemeval_s_cleaned.json` in the parent evaluation workspace.
- Selection: `question_id == "51a45a95"`.
- Declared evidence resolution: `answer_d61669c7` occurs exactly once, as the 43rd of 50 sessions.
- Unique anchor location: `coffee creamer`, `$5 coupon`, `redeemed`, user coupon mentions, and user mentions of `Target` all resolve only to `answer_d61669c7` within this question's haystack.
- Guardrail: assistant interpretations are secondary evidence and cannot create an unstated user fact.

No other haystack session supplies an alternate redemption location or another occurrence of the queried coupon event. The ambiguity is therefore inside the declared evidence session, not a conflict between competing sessions.

## 9. Raw-history evidence

The session strongly suggests Target through conversational continuity, but the location and redemption event appear in separate user turns. The only explicit bridge is the assistant's interpretation, followed by a user response that continues discussing Target without confirming the coupon's location.

| Turn | Role | `has_answer` | Raw evidence | Judgment |
|---|---|---:|---|---|
| 3 | User | `false` | "I've been using the Cartwheel app from Target" | Supplies the only user-stated store context for the later coupon event. |
| 5 | User | `true` | "I actually redeemed a $5 coupon on coffee creamer last Sunday" | Supplies the redemption event but does not name a store. |
| 6 | Assistant | `false` | "Many retailers, like Target, send exclusive coupons" and asks how often the user shops at Target | Makes the Target inference explicit, but assistant-generated interpretation is not an independent user fact. |
| 7 | User | `false` | "I shop at Target pretty frequently" | Continues the Target topic without correcting the assistant, but still does not link this coupon to Target. |

Reading the complete session as ordinary conversation supports Target: the user introduces Target's Cartwheel app, describes a coupon redemption, accepts the assistant's Target-focused continuation, and gives more Target-shopping detail. Reading only explicit propositions does not prove the redemption location because the decisive relation, "redeemed at Target," is never stated.

## 10. Evidence-label defect

The session-level `answer_session_ids` declaration is correct because `answer_d61669c7` contains both the Target context and the coupon event. The turn-level annotation is incomplete because its sole `has_answer: true` turn contains no location and therefore cannot independently support the gold answer.

A turn-level retriever following the current label receives the coupon turn but not the earlier Target-bearing user turn. To support the existing gold answer in a curated derivative dataset, both user turns are required evidence:

```text
Turn 3: Cartwheel app from Target
Turn 5: redeemed a $5 coupon on coffee creamer
```

Marking only Turn 5 as answer-bearing conflates evidence for the event with evidence for the requested attribute of that event. This defect can penalize a retriever that correctly needs both turns to answer where.

## 11. Adjudication

Official reproduction should preserve **Target** and the original dataset bytes, while interpretation-sensitive analysis should disclose that the answer is contextual rather than explicit. This avoids silently changing the benchmark while preventing the case from being presented as cleanly grounded evidence.

- Retain `Target` when reproducing published or comparable LongMemEval scores.
- Accept `Target` as the best natural-language answer when the full evidence session is available.
- Treat an abstaining answer such as "the session does not explicitly say" as evidence-sensitive rather than automatically diagnosing a memory-system failure.
- Classify the case as `gold-ambiguous` or `weakly-grounded` in dataset-quality reports.
- In a separately curated dataset, either rewrite the evidence to state the store directly or mark both the Target context and coupon event as answer-bearing turns.
