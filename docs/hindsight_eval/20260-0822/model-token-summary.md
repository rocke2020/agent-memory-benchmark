# model and token usages
462 question for orig ds, 38 fron tencent cloud ds.
from 2028-0819 to 0822, only at no peak time to save cost.
The cost, api request number and tokens include both OMB and HINDSIGHT_API usage.

## configs

OMB_ANSWER_LLM=openai
OMB_ANSWER_MODEL=deepseek-v4-pro
OMB_JUDGE_LLM=openai
OMB_JUDGE_MODEL=deepseek-v4-flash

exact version
deepseek-v4-flash-0731
deepseek-v4-pro-0813

DEEPSEEK_BASE_URL=https://api.deepseek.com

## orig ds costs and tokens
only resume 1 used tencent cloud ds and other use orig ds. from orig ds, totally 462 quetions used totally 1479 yuan.
Note: the api request number, tokens and costs include both OMB and HINDSIGHT_API usage.
### if full from orig ds
approximately totally 1600 yuan, api requests 118648, tokens 743,356,436.
### phase 0, 0->112, 112 questions finished
cost 362 yuan, api requests 26927, tokens 168,358,427.
### resume 1, 112->150, 38 questions finished
tencent cloud, so cannot know excat used tokens and cost of resume 1.
### resuem 2-3, 150->500, 350 questions finished
cost 1117 yuan, api requests 82704, tokens 518,502,920
