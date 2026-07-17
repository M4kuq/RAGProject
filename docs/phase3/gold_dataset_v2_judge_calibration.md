# Gold Dataset v2 縺ｨ陬懷勧 LLM Judge 譬｡豁｣蝓ｺ逶､

## 逶ｮ逧・
`Grounded Answer Pass Rate` 繧剃ｸｻ謖・ｨ吶↓縺吶ｋ縺溘ａ縺ｮ縲∝ｮ牙・縺ｧ豎ｺ螳夊ｫ也噪縺ｪGold dataset縲‘vidence catalog縲）udge rubric縲∽ｺｺ髢捺｡豁｣繝昴Μ繧ｷ繝ｼ繧貞ｮ夂ｾｩ縺励∪縺吶・縺薙・螟画峩縺ｯPR #91縺ｮMetric V2繧貞燕謠舌→縺吶ｋstacked change縺ｧ縺吶Ｓunner謗･邯壹ｄ螟夜ΚLLM蜻ｼ縺ｳ蜃ｺ縺励・蜷ｫ縺ｿ縺ｾ縺帙ｓ縲・
## Dataset balance

| 霆ｸ | 繧ｱ繝ｼ繧ｹ謨ｰ |
| --- | ---: |
| 蜷郁ｨ・| 50 |
| answerable / unanswerable | 30 / 20 |
| single-hop / multi-hop | 25 / 25 |
| hybrid / agentic_router | 25 / 25 |
| prompt injection | 10 |
| English / Japanese | 25 / 25 |

蜷・ase縺ｯ `answerable`縲～reference_answer`縲～required_facts`縲～forbidden_claims`縲～expected_evidence`縲～required_citation`縲～expected_strategy`縲～tags` 繧貞ｿ・亥｢・阜縺ｨ縺励※謖√■縺ｾ縺吶・`expected_evidence` 縺ｯ迺ｰ蠅・ｾ晏ｭ倥・DB ID縺ｧ縺ｯ縺ｪ縺上《ource catalog縺ｮ螳牙ｮ壹＠縺・`source_key` 縺ｨ `fact_id` 繧貞盾辣ｧ縺励∪縺吶・answerable case縺ｯ蜈ｨrequired fact繧痴upport evidence縺ｧ陲ｫ隕・＠縲「nanswerable case縺ｯnear-miss evidence縺ｨ遖∵ｭ｢荳ｻ蠑ｵ繧貞ｮ夂ｾｩ縺励∪縺吶・
## Primary metric

`Grounded Answer Pass Rate = hard gate繧貞・縺ｦ騾夐℃縺励◆case謨ｰ / 蜈ｨcase謨ｰ` 縺ｧ縺吶・
- answerable: required facts縲…itation support縲’orbidden claim absence繧貞ｿ・医↓縺吶ｋ
- unanswerable: correct abstention縲’orbidden claim absence繧貞ｿ・医↓縺吶ｋ
- citation蠢・・ase: citation support繧貞ｿ・医↓縺吶ｋ
- prompt injection case: injection resistance繧貞ｿ・医↓縺吶ｋ

蟷ｳ蝮・せ繧ｳ繧｢縺ｧhard failure繧堤嶌谿ｺ縺励∪縺帙ｓ縲・LM judge縺ｮconfidence繧ゆｸｻ謖・ｨ吶◎縺ｮ繧ゅ・縺ｫ縺ｯ豺ｷ縺懊∪縺帙ｓ縲・
## Existing evaluation runner adapter

`load_evaluation_cases("gold_answer_quality_v2", case_limit=50)` 縺ｯGold Dataset v2繧呈里蟄倥・ `EvaluationCase` 螂醍ｴ・∈螟画鋤縺励∪縺吶・
`EvaluationService.run_job()`縲『orker handler縲．B繝・・繝悶Ν縲∝・髢帰PI縺ｯ螟画峩縺帙★縲∵里蟄腕unner縺・0莉ｶ繧偵Ο繝ｼ繝峨＠縺ｦ譌｢蟄倥・豎ｺ螳夊ｫ也噪metric繧帝寔險医〒縺阪∪縺吶・

- required fact statement繧弾xpected keyword縺ｨanswer-completeness slot縺ｸ螟画鋤縺吶ｋ
- expected strategy縲”op count縲》ag繧呈里蟄倥・safe metadata蠅・阜縺ｸ蜀吝ワ縺吶ｋ
- reference answer縺ｯ螳溯｡梧凾縺ｮ豈碑ｼ・□縺代↓菴ｿ逕ｨ縺励．B縲、PI detail縲》race artifact縲√Ο繧ｰ縺ｸ菫晏ｭ倥＠縺ｪ縺・
- forbidden claim縲‘xpected evidence縲｝rompt繧池unner artifact縺ｸ隍・｣ｽ縺励↑縺・

邨ｱ蜷医ユ繧ｹ繝医・螟夜ΚLLM縲∝､夜Κjudge縲、WS縲～load-data` 繧剃ｽｿ繧上↑縺・盾辣ｧRAG stub縺ｧ50莉ｶ縺ｮ螳瑚ｵｰ縺ｨ髮・ｨ医ｒ讀懆ｨｼ縺励∪縺吶・
縺薙・謗･邯壹・譌｢蟄藁etric runner蜷代￠縺ｧ縺ゅｊ縲∬｣懷勧LLM judge縺ｮ蜻ｼ縺ｳ蜃ｺ縺励ｄ `Grounded Answer Pass Rate` 縺ｮjudge蛻､螳壹ｒ霑ｽ蜉縺励∪縺帙ｓ縲・

## Auxiliary judge 縺ｨ莠ｺ髢捺｡豁｣

LLM judge縺ｯ陬懷勧蛻､螳壹□縺代ｒ陦ｨ縺励∝､夜Κ蜻ｼ縺ｳ蜃ｺ縺怜ｮ溯｣・・縺薙・PR縺ｫ蜷ｫ繧√∪縺帙ｓ縲Ｅecision schema縺ｯ蛻玲嫌蛟､縲…onfidence縲《afe reason code縺縺代ｒ險ｱ蜿ｯ縺励〉aw answer縲〉aw context縲∬・逕ｱ險倩ｿｰrationale繧剃ｿ晏ｭ倥＠縺ｾ縺帙ｓ縲・
- 蛻晄悄譬｡豁｣: 100%莠ｺ髢鍋｢ｺ隱・- 騾壼ｸｸ驕狗畑: baseline縺ｨ縺ｮ蟾ｮ蛻・ｒ蜈ｨ莉ｶ遒ｺ隱・- hard gate failure繧貞・莉ｶ遒ｺ隱・- confidence 0.8譛ｪ貅繧貞・莉ｶ遒ｺ隱・- 谿九ｊ縺九ｉ豎ｺ螳夊ｫ也噪縺ｫ15%繧堤屮譟ｻ

逶｣譟ｻbucket縺ｯcase ID縺ｨevaluation fingerprint縺ｮSHA-256縺九ｉ豎ｺ螳壹＠縲∝・螳溯｡後〒蟇ｾ雎｡縺後・繧後↑縺・ｈ縺・↓縺励∪縺吶・
## Security boundary

- fixture縺ｯ譫ｶ遨ｺ縺ｮ螳牙・縺ｪ蛟､縺縺代ｒ菴ｿ逕ｨ縺吶ｋ
- secret assignment縲‘mail縲《ecret-shaped token繧致alidator縺ｧ諡貞凄縺吶ｋ
- prompt injection case縺ｧ繧ょｮ溽ｧ伜ｯ・､繧堤ｽｮ縺九↑縺・- judge/calibration artifact縺ｸraw prompt縲〉aw answer縲〉aw chunk縲’ull context繧定ｿｽ蜉縺励↑縺・
## Non-goals

- 螟夜ΚLLM judge API縺ｮ蜻ｼ縺ｳ蜃ｺ縺・- semantic judge繧辰I hard gate縺ｫ縺吶ｋ縺薙→
- DB migration繧・valuation result schema螟画峩

## Merge order

1. PR #91繧貞・縺ｫmerge縺吶ｋ縲・2. 縺薙・stacked PR縺ｸ譛譁ｰmain繧帝壼ｸｸmerge縺励｜ase繧知ain縺ｸ螟画峩縺吶ｋ縲・3. 蠕檎ｶ啀R縺ｧ莠ｺ髢途eview UI繧貞ｰ上＆縺乗磁邯壹☆繧九・

