# LLM Paper Corpus for RAG Demo

This corpus was prepared on 2026-05-22 for local RAG testing. It contains 110 public paper notes about foundational, famous, and recent LLM-related work. Each entry is a short original summary, not a copied abstract. The goal is to give the RAG demo enough grounded material to answer questions about LLM architecture, training, alignment, retrieval, evaluation, agents, multimodality, coding, and efficient inference.

## Coverage

- Foundational transformer and language-modeling papers.
- Scaling, instruction tuning, preference optimization, and alignment papers.
- RAG, retrieval, tool use, agent, benchmark, multimodal, code, and efficient inference papers.
- Recent 2024-2026 reports and surveys, including DeepSeek-R1, Kimi k1.5, Qwen2.5, Qwen3, Qwen2.5-VL, and 2026 LLM survey material.

## Paper Notes

### P001 Attention Is All You Need (2017)
- Source: https://arxiv.org/abs/1706.03762
- Area: architecture, transformer.
- Overview: Transformer replaced recurrence and convolution with attention-centered sequence modeling.
- Technical content: Multi-head self-attention, positional encoding, residual blocks, and feed-forward layers made parallel training practical.
- Principle: Model quality can come from flexible token interaction rather than sequential processing.

### P002 Improving Language Understanding by Generative Pre-Training (2018)
- Source: https://cdn.openai.com/research-covers/language-unsupervised/language_understanding_paper.pdf
- Area: generative pretraining.
- Overview: GPT-1 showed that a decoder transformer pretrained on text could transfer to many NLP tasks.
- Technical content: Unsupervised next-token pretraining is followed by supervised fine-tuning.
- Principle: General language competence can be acquired before task-specific adaptation.

### P003 BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding (2018)
- Source: https://arxiv.org/abs/1810.04805
- Area: encoder pretraining.
- Overview: BERT made bidirectional masked-language pretraining central to language understanding.
- Technical content: Masked language modeling and next sentence prediction support fine-tuning with small task heads.
- Principle: Deep contextual representations improve classification, inference, and extractive QA.

### P004 Language Models are Unsupervised Multitask Learners (2019)
- Source: https://cdn.openai.com/better-language-models/language_models_are_unsupervised_multitask_learners.pdf
- Area: decoder language models.
- Overview: GPT-2 demonstrated broad zero-shot behavior from large web-scale language modeling.
- Technical content: A decoder-only transformer learns task patterns from natural text without explicit task labels.
- Principle: Scale and diverse data can make tasks look like text continuation.

### P005 RoBERTa: A Robustly Optimized BERT Pretraining Approach (2019)
- Source: https://arxiv.org/abs/1907.11692
- Area: encoder pretraining.
- Overview: RoBERTa showed that BERT performance depended strongly on data, batch size, and training procedure.
- Technical content: It removed next sentence prediction and trained longer on more data with dynamic masking.
- Principle: Optimization choices can matter as much as architecture changes.

### P006 XLNet: Generalized Autoregressive Pretraining for Language Understanding (2019)
- Source: https://arxiv.org/abs/1906.08237
- Area: pretraining objective.
- Overview: XLNet combined autoregressive likelihood with bidirectional context through permutation language modeling.
- Technical content: It builds on Transformer-XL recurrence to model long dependencies.
- Principle: Objective design can recover bidirectional information without masked-token mismatch.

### P007 Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer (T5, 2019)
- Source: https://arxiv.org/abs/1910.10683
- Area: text-to-text transfer.
- Overview: T5 framed many NLP tasks as text-to-text generation.
- Technical content: A unified encoder-decoder model is trained with span corruption on the C4 corpus.
- Principle: A single interface simplifies task formulation and transfer.

### P008 Megatron-LM: Training Multi-Billion Parameter Language Models Using Model Parallelism (2019)
- Source: https://arxiv.org/abs/1909.08053
- Area: distributed training.
- Overview: Megatron-LM showed practical tensor model parallelism for very large transformers.
- Technical content: It partitions attention and feed-forward layers across GPUs.
- Principle: Systems design is part of model capability scaling.

### P009 Scaling Laws for Neural Language Models (2020)
- Source: https://arxiv.org/abs/2001.08361
- Area: scaling laws.
- Overview: This work quantified how loss improves with model size, data, and compute.
- Technical content: Empirical power laws relate compute allocation to language-model performance.
- Principle: Predictable scaling made large training runs more planful.

### P010 Language Models are Few-Shot Learners (GPT-3, 2020)
- Source: https://arxiv.org/abs/2005.14165
- Area: in-context learning.
- Overview: GPT-3 showed that large decoder LMs can perform tasks from prompts and examples.
- Technical content: A 175B parameter model is evaluated zero-shot, one-shot, and few-shot without gradient updates.
- Principle: Prompting can become an interface for adaptation.

### P011 Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (2020)
- Source: https://arxiv.org/abs/2005.11401
- Area: RAG.
- Overview: RAG connected neural retrieval with sequence generation for evidence-grounded answers.
- Technical content: A retriever selects passages that condition a generator at inference time.
- Principle: External knowledge can reduce reliance on model memory alone.

### P012 REALM: Retrieval-Augmented Language Model Pre-Training (2020)
- Source: https://arxiv.org/abs/2002.08909
- Area: retrieval pretraining.
- Overview: REALM integrated retrieval into language-model pretraining.
- Technical content: The model learns to retrieve documents that improve masked-token prediction.
- Principle: Retrieval can be learned as part of the language-model objective.

### P013 Dense Passage Retrieval for Open-Domain Question Answering (2020)
- Source: https://arxiv.org/abs/2004.04906
- Area: dense retrieval.
- Overview: DPR popularized dual-encoder dense retrieval for QA.
- Technical content: Separate question and passage encoders are trained with contrastive objectives.
- Principle: Semantic retrieval can outperform sparse matching for open-domain QA.

### P014 ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction (2020)
- Source: https://arxiv.org/abs/2004.12832
- Area: retrieval.
- Overview: ColBERT balanced dense semantic matching with token-level late interaction.
- Technical content: It stores contextual token embeddings and uses MaxSim scoring.
- Principle: Retrieval can preserve fine-grained lexical evidence without full cross-encoding cost.

### P015 Fusion-in-Decoder for Open-Domain Question Answering (2020)
- Source: https://arxiv.org/abs/2007.01282
- Area: retrieval-augmented generation.
- Overview: FiD improved answer generation by encoding multiple retrieved passages separately.
- Technical content: The decoder attends over fused encoded passages.
- Principle: Generators can synthesize evidence from several documents.

### P016 Switch Transformers: Scaling to Trillion Parameter Models with Simple and Efficient Sparsity (2021)
- Source: https://arxiv.org/abs/2101.03961
- Area: mixture of experts.
- Overview: Switch Transformer scaled sparse expert models with simple routing.
- Technical content: Each token routes to one expert, lowering active compute per token.
- Principle: Sparse capacity can increase model size without proportional inference cost.

### P017 Prefix-Tuning: Optimizing Continuous Prompts for Generation (2021)
- Source: https://arxiv.org/abs/2101.00190
- Area: parameter-efficient tuning.
- Overview: Prefix-tuning adapts large models by learning small continuous prefixes.
- Technical content: It prepends trainable vectors to transformer activations while freezing most weights.
- Principle: Many adaptations do not need full model fine-tuning.

### P018 LoRA: Low-Rank Adaptation of Large Language Models (2021)
- Source: https://arxiv.org/abs/2106.09685
- Area: parameter-efficient tuning.
- Overview: LoRA became a standard way to fine-tune large models cheaply.
- Technical content: It injects trainable low-rank matrices into selected linear layers.
- Principle: Weight updates for adaptation often live in a low-dimensional subspace.

### P019 Evaluating Large Language Models Trained on Code (Codex, 2021)
- Source: https://arxiv.org/abs/2107.03374
- Area: code generation.
- Overview: Codex showed strong program synthesis behavior from code-trained LMs.
- Technical content: HumanEval measures pass rates for generated Python functions.
- Principle: Code is a high-value domain for evaluating reasoning and tool-like generation.

### P020 Program Synthesis with Large Language Models (2021)
- Source: https://arxiv.org/abs/2108.07732
- Area: code generation.
- Overview: This work studied large LMs as program synthesizers across programming tasks.
- Technical content: It evaluates generation, sampling, and reranking for code solutions.
- Principle: Multiple sampled attempts can convert uncertain generation into useful synthesis.

### P021 Finetuned Language Models Are Zero-Shot Learners (FLAN, 2021)
- Source: https://arxiv.org/abs/2109.01652
- Area: instruction tuning.
- Overview: FLAN showed that instruction tuning across tasks improves zero-shot generalization.
- Technical content: The model is fine-tuned on natural-language task instructions.
- Principle: Instructions are reusable supervision across task families.

### P022 TruthfulQA: Measuring How Models Mimic Human Falsehoods (2021)
- Source: https://arxiv.org/abs/2109.07958
- Area: evaluation, truthfulness.
- Overview: TruthfulQA tests whether models repeat common misconceptions.
- Technical content: Questions are designed so imitation of web text can lead to false answers.
- Principle: Fluency and truthfulness are different capabilities.

### P023 Multitask Prompted Training Enables Zero-Shot Task Generalization (T0, 2021)
- Source: https://arxiv.org/abs/2110.08207
- Area: prompted multitask training.
- Overview: T0 used prompted datasets to train models that generalize to unseen tasks.
- Technical content: Many datasets are converted into natural-language prompt templates.
- Principle: Prompt format diversity improves instruction-following transfer.

### P024 Training Verifiers to Solve Math Word Problems (GSM8K, 2021)
- Source: https://arxiv.org/abs/2110.14168
- Area: math reasoning, evaluation.
- Overview: GSM8K became a core benchmark for grade-school mathematical reasoning.
- Technical content: It uses multi-step word problems and answer verification.
- Principle: Reasoning evaluation needs tasks where the final answer can be checked.

### P025 RETRO: Improving Language Models by Retrieving from Trillions of Tokens (2021)
- Source: https://arxiv.org/abs/2112.04426
- Area: retrieval-augmented pretraining.
- Overview: RETRO showed retrieval can improve language modeling at large scale.
- Technical content: A model attends to nearest-neighbor chunks from a huge text database.
- Principle: Retrieval can substitute for some parametric memory.

### P026 WebGPT: Browser-assisted Question-Answering with Human Feedback (2021)
- Source: https://arxiv.org/abs/2112.09332
- Area: web retrieval, alignment.
- Overview: WebGPT combined browser use, citations, and human preference training.
- Technical content: Models browse, quote sources, and are trained with demonstrations and comparisons.
- Principle: Grounded answers need both retrieval behavior and preference shaping.

### P027 GLaM: Efficient Scaling of Language Models with Mixture-of-Experts (2021)
- Source: https://arxiv.org/abs/2112.06905
- Area: mixture of experts.
- Overview: GLaM showed sparse MoE models can reach high quality with lower active compute.
- Technical content: Tokens route to experts in a large sparse transformer.
- Principle: Capacity and compute can be decoupled.

### P028 Scaling Language Models: Methods, Analysis and Insights from Training Gopher (2021)
- Source: https://arxiv.org/abs/2112.11446
- Area: scaling, evaluation.
- Overview: Gopher analyzed large-scale training and broad benchmark behavior.
- Technical content: It studies a 280B parameter model across language, knowledge, and reasoning tasks.
- Principle: Capability growth is uneven across domains and needs broad evaluation.

### P029 Chain-of-Thought Prompting Elicits Reasoning in Large Language Models (2022)
- Source: https://arxiv.org/abs/2201.11903
- Area: prompting, reasoning.
- Overview: Chain-of-thought prompting made intermediate reasoning text a core technique.
- Technical content: Few-shot examples include step-by-step rationales before answers.
- Principle: Large models can solve harder tasks when prompted to decompose reasoning.

### P030 Training Language Models to Follow Instructions with Human Feedback (InstructGPT, 2022)
- Source: https://arxiv.org/abs/2203.02155
- Area: alignment, RLHF.
- Overview: InstructGPT aligned GPT-3 behavior with user instructions using human feedback.
- Technical content: It uses supervised demonstrations, reward modeling, and PPO optimization.
- Principle: Smaller aligned models can be preferred over larger unaligned models.

### P031 Training Compute-Optimal Large Language Models (Chinchilla, 2022)
- Source: https://arxiv.org/abs/2203.15556
- Area: scaling laws.
- Overview: Chinchilla argued many large models were undertrained relative to their size.
- Technical content: It proposes a better balance between parameters and training tokens.
- Principle: More data can beat simply increasing parameter count.

### P032 PaLM: Scaling Language Modeling with Pathways (2022)
- Source: https://arxiv.org/abs/2204.02311
- Area: large-scale LLM.
- Overview: PaLM demonstrated strong few-shot, reasoning, and multilingual performance at 540B parameters.
- Technical content: It uses Pathways infrastructure and large-scale decoder transformer training.
- Principle: Infrastructure and model scale jointly expand general capabilities.

### P033 SayCan: Do As I Can, Not As I Say (2022)
- Source: https://arxiv.org/abs/2204.01691
- Area: robotics, grounded agents.
- Overview: SayCan linked language-model planning with affordance scores from robots.
- Technical content: A language model proposes actions while value functions judge what is feasible.
- Principle: Agents need grounding in environment capability, not language plausibility alone.

### P034 Least-to-Most Prompting Enables Complex Reasoning (2022)
- Source: https://arxiv.org/abs/2205.10625
- Area: prompting, reasoning.
- Overview: Least-to-most prompting decomposes hard problems into ordered subproblems.
- Technical content: The model first creates simpler steps and then solves them sequentially.
- Principle: Decomposition improves generalization on compositional tasks.

### P035 OPT: Open Pre-trained Transformer Language Models (2022)
- Source: https://arxiv.org/abs/2205.01068
- Area: open models.
- Overview: OPT released large decoder models and training logs for research reproducibility.
- Technical content: The family includes models up to 175B parameters.
- Principle: Open artifacts help the community study large-scale LMs.

### P036 UL2: Unifying Language Learning Paradigms (2022)
- Source: https://arxiv.org/abs/2205.05131
- Area: pretraining objective.
- Overview: UL2 unified several denoising and language modeling modes.
- Technical content: Mixture-of-denoisers training supports different generation and understanding behaviors.
- Principle: Objective mixtures can make a model more versatile.

### P037 Self-Consistency Improves Chain of Thought Reasoning (2022)
- Source: https://arxiv.org/abs/2203.11171
- Area: decoding, reasoning.
- Overview: Self-consistency samples multiple reasoning paths and chooses the most common answer.
- Technical content: It replaces greedy decoding with answer aggregation over diverse chains.
- Principle: Reasoning uncertainty can be reduced by sampling and voting.

### P038 Emergent Abilities of Large Language Models (2022)
- Source: https://arxiv.org/abs/2206.07682
- Area: scaling behavior.
- Overview: This paper described abilities that appear sharply at larger scale.
- Technical content: It compares benchmark performance across model sizes.
- Principle: Scaling can change qualitative behavior, though measurement choices matter.

### P039 BIG-bench: Beyond the Imitation Game Benchmark (2022)
- Source: https://arxiv.org/abs/2206.04615
- Area: evaluation.
- Overview: BIG-bench collected many tasks to probe capabilities and limitations.
- Technical content: It includes diverse language, reasoning, social, and symbolic tasks.
- Principle: LLM evaluation needs breadth beyond a few standard datasets.

### P040 Minerva: Solving Quantitative Reasoning Problems with Language Models (2022)
- Source: https://arxiv.org/abs/2206.14858
- Area: math, science reasoning.
- Overview: Minerva focused on mathematical and scientific problem solving.
- Technical content: It trains on technical web pages and scientific papers with equation-rich data.
- Principle: Domain-specific data improves specialist reasoning.

### P041 Atlas: Few-shot Learning with Retrieval Augmented Language Models (2022)
- Source: https://arxiv.org/abs/2208.03299
- Area: RAG, few-shot learning.
- Overview: Atlas showed retrieval-augmented models can perform strong few-shot QA.
- Technical content: It jointly trains retriever and generator for knowledge-intensive tasks.
- Principle: Retrieval helps smaller models compete on knowledge tasks.

### P042 Constitutional AI: Harmlessness from AI Feedback (2022)
- Source: https://arxiv.org/abs/2212.08073
- Area: alignment, safety.
- Overview: Constitutional AI used written principles and AI feedback to reduce harmful behavior.
- Technical content: Models critique and revise responses before preference-style training.
- Principle: Alignment can use explicit normative rules rather than only human labels.

### P043 Self-Instruct: Aligning Language Models with Self-Generated Instructions (2022)
- Source: https://arxiv.org/abs/2212.10560
- Area: instruction data.
- Overview: Self-Instruct used a model to generate instruction-following data.
- Technical content: It bootstraps tasks, inputs, and outputs, then filters generated examples.
- Principle: Synthetic data can reduce dependence on human annotation.

### P044 HyDE: Precise Zero-Shot Dense Retrieval without Relevance Labels (2022)
- Source: https://arxiv.org/abs/2212.10496
- Area: retrieval.
- Overview: HyDE improves retrieval by generating a hypothetical answer document from the query.
- Technical content: The generated document is embedded and used to retrieve real documents.
- Principle: Generation can bridge query-document semantic gaps.

### P045 BLOOM: A 176B-Parameter Open-Access Multilingual Language Model (2022)
- Source: https://arxiv.org/abs/2211.05100
- Area: open multilingual LLM.
- Overview: BLOOM provided an open-access multilingual model trained by a large collaboration.
- Technical content: It emphasizes governance, multilingual data, and reproducibility.
- Principle: Large LMs can be built through open scientific collaboration.

### P046 Galactica: A Large Language Model for Science (2022)
- Source: https://arxiv.org/abs/2211.09085
- Area: scientific language models.
- Overview: Galactica explored language modeling over scientific knowledge and papers.
- Technical content: It trained on papers, references, equations, and scientific text.
- Principle: Scientific assistants need domain data but still require careful reliability controls.

### P047 Toolformer: Language Models Can Teach Themselves to Use Tools (2023)
- Source: https://arxiv.org/abs/2302.04761
- Area: tool use.
- Overview: Toolformer taught models to call APIs through self-supervised data generation.
- Technical content: The model inserts tool calls and keeps examples that improve prediction likelihood.
- Principle: Tool use can be learned from model-generated supervision.

### P048 LLaMA: Open and Efficient Foundation Language Models (2023)
- Source: https://arxiv.org/abs/2302.13971
- Area: open foundation models.
- Overview: LLaMA showed strong performance from efficiently trained open-weight models.
- Technical content: It scales data quality and token count for models from 7B to 65B.
- Principle: Smaller open models can be competitive when trained well.

### P049 Reflexion: Language Agents with Verbal Reinforcement Learning (2023)
- Source: https://arxiv.org/abs/2303.11366
- Area: agents, self-reflection.
- Overview: Reflexion lets agents improve by storing verbal feedback from previous attempts.
- Technical content: It uses episodic memory and self-evaluation rather than weight updates.
- Principle: Iterative reflection can improve behavior during inference.

### P050 GPT-4 Technical Report (2023)
- Source: https://arxiv.org/abs/2303.08774
- Area: frontier model report.
- Overview: GPT-4 reported broad improvements in reasoning, exams, and multimodal capability.
- Technical content: It describes evaluation and safety work while withholding many training details.
- Principle: Frontier reporting often balances transparency with deployment risk management.

### P051 Sparks of Artificial General Intelligence: Early Experiments with GPT-4 (2023)
- Source: https://arxiv.org/abs/2303.12712
- Area: capability analysis.
- Overview: This work explored GPT-4 behavior across broad reasoning and creative tasks.
- Technical content: It uses qualitative probes to study generalization and abstraction.
- Principle: New capabilities may require exploratory evaluation beyond benchmark scores.

### P052 Generative Agents: Interactive Simulacra of Human Behavior (2023)
- Source: https://arxiv.org/abs/2304.03442
- Area: agents, simulation.
- Overview: Generative Agents used LLMs to simulate believable social behavior.
- Technical content: Memory streams, reflection, and planning drive agent actions.
- Principle: Long-lived agents need memory and planning around language models.

### P053 Tree of Thoughts: Deliberate Problem Solving with Large Language Models (2023)
- Source: https://arxiv.org/abs/2305.10601
- Area: reasoning search.
- Overview: Tree of Thoughts treats reasoning as search over intermediate thoughts.
- Technical content: The model proposes, evaluates, and explores multiple branches.
- Principle: Deliberation can be structured as a search process.

### P054 QLoRA: Efficient Finetuning of Quantized LLMs (2023)
- Source: https://arxiv.org/abs/2305.14314
- Area: efficient fine-tuning.
- Overview: QLoRA made instruction tuning large models feasible on limited hardware.
- Technical content: It backpropagates through 4-bit quantized weights into LoRA adapters.
- Principle: Quantization and adapters can democratize model adaptation.

### P055 Gorilla: Large Language Model Connected with Massive APIs (2023)
- Source: https://arxiv.org/abs/2305.15334
- Area: tool/API use.
- Overview: Gorilla focused on selecting and calling APIs accurately.
- Technical content: It trains on API documentation and evaluates tool-call correctness.
- Principle: Reliable tool use requires grounding in structured documentation.

### P056 Direct Preference Optimization (DPO, 2023)
- Source: https://arxiv.org/abs/2305.18290
- Area: preference optimization.
- Overview: DPO simplified RLHF-style preference training without explicit reward modeling and PPO.
- Technical content: It optimizes a classification-style objective over preferred and rejected responses.
- Principle: Alignment training can be simplified when preference data is available.

### P057 Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (2023)
- Source: https://arxiv.org/abs/2306.05685
- Area: evaluation.
- Overview: This work studied strong LLMs as evaluators of chat assistants.
- Technical content: MT-Bench and Arena-style comparisons measure multi-turn assistant quality.
- Principle: Automated judges are useful but require bias and calibration checks.

### P058 WebArena: A Realistic Web Environment for Building Autonomous Agents (2023)
- Source: https://arxiv.org/abs/2307.13854
- Area: web agents.
- Overview: WebArena evaluates agents on realistic websites and tasks.
- Technical content: It provides browser-based environments requiring navigation and action.
- Principle: Agent evaluation should include interactive real-world workflows.

### P059 ToolBench: An Open Platform for Training, Serving, and Evaluating Tool-Using LLMs (2023)
- Source: https://arxiv.org/abs/2307.16789
- Area: tool use.
- Overview: ToolBench created data and evaluation for many API-using tasks.
- Technical content: It uses tool-augmented instructions and evaluates successful tool chains.
- Principle: Tool-use models need both planning and API selection skill.

### P060 Llama 2: Open Foundation and Fine-Tuned Chat Models (2023)
- Source: https://arxiv.org/abs/2307.09288
- Area: open chat models.
- Overview: Llama 2 released open foundation and chat models with safety evaluations.
- Technical content: It uses supervised fine-tuning and reinforcement learning from human feedback.
- Principle: Open models can include explicit safety and alignment reporting.

### P061 MetaGPT: Meta Programming for Multi-Agent Collaborative Framework (2023)
- Source: https://arxiv.org/abs/2308.00352
- Area: multi-agent systems.
- Overview: MetaGPT organized LLM agents around software-team roles.
- Technical content: It encodes standard operating procedures for product, architecture, and coding roles.
- Principle: Multi-agent systems need process structure, not just multiple chatbots.

### P062 AgentBench: Evaluating LLMs as Agents (2023)
- Source: https://arxiv.org/abs/2308.03688
- Area: agent evaluation.
- Overview: AgentBench benchmarks LLMs across interactive environments.
- Technical content: Tasks include games, web, databases, operating systems, and embodied settings.
- Principle: Agent ability is broader than single-turn text generation.

### P063 AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation (2023)
- Source: https://arxiv.org/abs/2308.08155
- Area: agent framework.
- Overview: AutoGen introduced programmable multi-agent conversation patterns.
- Technical content: Agents can be configured for roles, tools, and human-in-the-loop workflows.
- Principle: Application orchestration matters for practical LLM systems.

### P064 Code Llama: Open Foundation Models for Code (2023)
- Source: https://arxiv.org/abs/2308.12950
- Area: code LLM.
- Overview: Code Llama adapted Llama models to code generation and infilling.
- Technical content: Variants include base, Python-specialized, and instruction-following code models.
- Principle: Domain adaptation improves programming performance.

### P065 Mistral 7B (2023)
- Source: https://arxiv.org/abs/2310.06825
- Area: efficient open models.
- Overview: Mistral 7B showed a small open model could compete with larger baselines.
- Technical content: It uses grouped-query attention and sliding-window attention.
- Principle: Architecture and training quality can make compact models strong.

### P066 SWE-bench: Can Language Models Resolve Real-World GitHub Issues? (2023)
- Source: https://arxiv.org/abs/2310.06770
- Area: software engineering evaluation.
- Overview: SWE-bench evaluates whether models can fix real repository issues.
- Technical content: Tasks use GitHub issues and tests from real projects.
- Principle: Code agents must modify whole repositories, not just synthesize small functions.

### P067 Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection (2023)
- Source: https://arxiv.org/abs/2310.11511
- Area: RAG, self-critique.
- Overview: Self-RAG teaches models when to retrieve and how to critique generated answers.
- Technical content: Reflection tokens guide retrieval, evidence use, and answer quality.
- Principle: RAG systems should decide when evidence is needed.

### P068 Mixtral of Experts (2024)
- Source: https://arxiv.org/abs/2401.04088
- Area: sparse MoE.
- Overview: Mixtral combined open-weight accessibility with sparse expert routing.
- Technical content: The model activates a subset of experts per token.
- Principle: MoE can deliver strong quality with lower active compute.

### P069 DeepSeek LLM: Scaling Open-Source Language Models with Longtermism (2024)
- Source: https://arxiv.org/abs/2401.02954
- Area: open LLM training.
- Overview: DeepSeek LLM described data, scaling, alignment, and evaluation for open models.
- Technical content: It reports base and chat models with scaling-law analysis.
- Principle: Open training reports help compare data, compute, and alignment choices.

### P070 Corrective Retrieval Augmented Generation (CRAG, 2024)
- Source: https://arxiv.org/abs/2401.15884
- Area: RAG reliability.
- Overview: CRAG adds correction mechanisms when retrieved evidence is weak or wrong.
- Technical content: It evaluates retrieval quality and supplements with external search or refinement.
- Principle: RAG needs failure handling, not just retrieval.

### P071 RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval (2024)
- Source: https://arxiv.org/abs/2401.18059
- Area: hierarchical RAG.
- Overview: RAPTOR builds a tree of summaries for long-document retrieval.
- Technical content: It clusters chunks, summarizes clusters, and retrieves across tree levels.
- Principle: Multi-scale summaries help questions that require broad context.

### P072 DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models (2024)
- Source: https://arxiv.org/abs/2402.03300
- Area: mathematical reasoning.
- Overview: DeepSeekMath improved open math reasoning through data and training design.
- Technical content: It combines mathematical pretraining data, instruction tuning, and reinforcement learning.
- Principle: Verifiable domains are strong targets for reasoning improvement.

### P073 Gemma: Open Models Based on Gemini Research and Technology (2024)
- Source: https://arxiv.org/abs/2403.08295
- Area: open models.
- Overview: Gemma released practical open models derived from Gemini-era research.
- Technical content: It emphasizes model cards, safety filtering, and responsible release.
- Principle: Open model releases need usability and safety documentation.

### P074 Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference (2024)
- Source: https://arxiv.org/abs/2403.04132
- Area: evaluation.
- Overview: Chatbot Arena uses pairwise human votes to rank chat models.
- Technical content: Elo-style ratings summarize anonymous head-to-head comparisons.
- Principle: Real user preference complements static benchmark scores.

### P075 GraphRAG: From Local to Global Query-Focused Summarization (2024)
- Source: https://arxiv.org/abs/2404.16130
- Area: graph RAG.
- Overview: GraphRAG organizes entities and relationships to support global summarization.
- Technical content: It builds a graph index and community summaries over document collections.
- Principle: Some questions need relational structure, not only vector similarity.

### P076 Many-Shot In-Context Learning (2024)
- Source: https://arxiv.org/abs/2405.09798
- Area: long-context learning.
- Overview: Many-shot ICL studies how long-context models benefit from many examples.
- Technical content: It evaluates multimodal models with hundreds or thousands of demonstrations.
- Principle: Long context can become a lightweight adaptation mechanism.

### P077 SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering (2024)
- Source: https://arxiv.org/abs/2405.15793
- Area: coding agents.
- Overview: SWE-agent improved software issue solving with a dedicated agent-computer interface.
- Technical content: It structures file viewing, editing, and test execution for LLM agents.
- Principle: Good tools and interfaces can matter as much as the base model.

### P078 The Llama 3 Herd of Models (2024)
- Source: https://arxiv.org/abs/2407.21783
- Area: open model family.
- Overview: Llama 3 reported a broad family of open models and post-training methods.
- Technical content: It covers data, scaling, safety, multilinguality, tool use, and evaluations.
- Principle: Model families are platforms with base, instruct, safety, and ecosystem components.

### P079 Gemma 2: Improving Open Language Models at a Practical Size (2024)
- Source: https://arxiv.org/abs/2408.00118
- Area: practical open models.
- Overview: Gemma 2 focused on strong quality at deployable sizes.
- Technical content: It uses architectural and training refinements to improve efficiency.
- Principle: Practical model size is a central design target for local and enterprise deployment.

### P080 Qwen2.5 Technical Report (2024)
- Source: https://arxiv.org/abs/2412.15115
- Area: model family, multilingual LLM.
- Overview: Qwen2.5 expanded the Qwen family across sizes and use cases.
- Technical content: It scaled high-quality pretraining data and multistage post-training.
- Principle: Broad model families support general, code, math, and multimodal specialization.

### P081 DeepSeek-V3 Technical Report (2024)
- Source: https://arxiv.org/abs/2412.19437
- Area: MoE, efficient frontier training.
- Overview: DeepSeek-V3 reported a large sparse model with strong cost-performance.
- Technical content: It uses mixture-of-experts design and training optimizations.
- Principle: Frontier capability can be pursued through efficient sparse architectures.

### P082 Qwen2.5-1M Technical Report (2025)
- Source: https://arxiv.org/abs/2501.15383
- Area: long context.
- Overview: Qwen2.5-1M extended model context lengths to one million tokens.
- Technical content: It combines long-context training, length extrapolation, sparse attention, and prefill optimization.
- Principle: Long-context systems need both model training and inference engineering.

### P083 DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning (2025)
- Source: https://arxiv.org/abs/2501.12948
- Area: reasoning, reinforcement learning.
- Overview: DeepSeek-R1 showed strong reasoning from large-scale RL and distilled smaller models.
- Technical content: R1-Zero uses RL directly, while R1 adds cold-start data and multistage training.
- Principle: Verifiable rewards can elicit reasoning behavior without relying only on human rationale data.

### P084 Kimi k1.5: Scaling Reinforcement Learning with LLMs (2025)
- Source: https://arxiv.org/abs/2501.12599
- Area: reasoning, multimodal RL.
- Overview: Kimi k1.5 scaled reinforcement learning for long-context multimodal reasoning.
- Technical content: It emphasizes policy optimization, long context, and long-to-short reasoning transfer.
- Principle: RL can become a scaling axis beyond next-token pretraining.

### P085 Qwen2.5-VL Technical Report (2025)
- Source: https://arxiv.org/abs/2502.13923
- Area: vision-language models.
- Overview: Qwen2.5-VL improved document, chart, video, and visual agent understanding.
- Technical content: It uses dynamic resolution processing, temporal encoding, and strong object localization.
- Principle: Multimodal LLMs need spatial and temporal grounding.

### P086 Qwen2.5-Omni Technical Report (2025)
- Source: https://arxiv.org/abs/2503.20215
- Area: omni-modal models.
- Overview: Qwen2.5-Omni handles text, image, audio, video, and streaming speech output.
- Technical content: It uses a Thinker-Talker architecture and time-aligned multimodal position encoding.
- Principle: Real-time assistants need integrated perception and generation across modalities.

### P087 Vision-R1: Incentivizing Reasoning Capability in Multimodal Large Language Models (2025)
- Source: https://arxiv.org/abs/2503.06749
- Area: multimodal reasoning.
- Overview: Vision-R1 adapts RL-style reasoning ideas to vision-language models.
- Technical content: It constructs multimodal chain-of-thought data and filters it for training.
- Principle: Reasoning supervision can cross from text-only models into multimodal settings.

### P088 A Survey of Frontiers in LLM Reasoning (2025)
- Source: https://huggingface.co/papers/2504.09037
- Area: reasoning survey.
- Overview: This survey organizes reasoning work across inference scaling, learning to reason, and agents.
- Technical content: It compares prompt-time methods, RL-based training, architectures, and agentic workflows.
- Principle: LLM reasoning is shifting from prompting alone toward training and systems design.

### P089 Retrieval Augmented Generation Evaluation in the Era of LLMs (2025)
- Source: https://arxiv.org/abs/2504.14891
- Area: RAG evaluation.
- Overview: This survey summarizes evaluation methods for RAG systems.
- Technical content: It covers factuality, retrieval quality, faithfulness, robustness, safety, and efficiency.
- Principle: RAG quality must be measured across the whole retrieval-generation pipeline.

### P090 Qwen3 Technical Report (2025)
- Source: https://arxiv.org/abs/2505.09388
- Area: model family, reasoning.
- Overview: Qwen3 advanced the Qwen family with stronger reasoning, multilinguality, and efficiency.
- Technical content: It includes dense and MoE models, thinking modes, and broad post-training.
- Principle: Modern model families combine general chat, reasoning, coding, and deployment profiles.

### P091 Retrieval-Augmented Generation: Architectures, Enhancements, and Robustness Frontiers (2025)
- Source: https://arxiv.org/abs/2506.00054
- Area: RAG survey.
- Overview: This survey reviews RAG architectures and robustness issues.
- Technical content: It discusses indexing, retrieval, fusion, generation, enhancement, and attack surfaces.
- Principle: RAG is a system architecture, not a single model trick.

### P092 Qwen3-VL Technical Report (2025)
- Source: https://arxiv.org/abs/2511.21631
- Area: vision-language models.
- Overview: Qwen3-VL extends the Qwen vision-language line with stronger multimodal performance.
- Technical content: It targets broad visual, document, and agentic multimodal tasks.
- Principle: Multimodal assistants increasingly require document and UI understanding.

### P093 A Survey of Large Language Models (2026)
- Source: https://link.springer.com/article/10.1007/s11704-026-60308-3
- Area: LLM survey.
- Overview: This 2026 survey reviews LLM development across pretraining, post-training, utilization, and evaluation.
- Technical content: It synthesizes architecture, data, alignment, reasoning, agents, and benchmark trends.
- Principle: LLM progress is best understood as an ecosystem of model, data, training, use, and evaluation.

### P094 Mamba: Linear-Time Sequence Modeling with Selective State Spaces (2023)
- Source: https://arxiv.org/abs/2312.00752
- Area: sequence architecture.
- Overview: Mamba proposed selective state-space models as an alternative to attention-heavy sequence modeling.
- Technical content: It uses input-dependent state-space parameters and hardware-aware kernels.
- Principle: Efficient long-sequence modeling may require architectures beyond standard attention.

### P095 RWKV: Reinventing RNNs for the Transformer Era (2023)
- Source: https://arxiv.org/abs/2305.13048
- Area: alternative architecture.
- Overview: RWKV blends recurrent inference with transformer-like training behavior.
- Technical content: It uses time-mixing and channel-mixing blocks to support efficient generation.
- Principle: LLM architecture design can revisit recurrence with modern scaling practices.

### P096 FlashAttention: Fast and Memory-Efficient Exact Attention (2022)
- Source: https://arxiv.org/abs/2205.14135
- Area: efficient attention.
- Overview: FlashAttention made exact attention faster and more memory efficient.
- Technical content: It tiles attention computation to reduce GPU memory traffic.
- Principle: Kernel-level systems work directly affects feasible context length and throughput.

### P097 FlashAttention-2: Faster Attention with Better Parallelism (2023)
- Source: https://arxiv.org/abs/2307.08691
- Area: efficient attention.
- Overview: FlashAttention-2 improved attention speed through better parallelization.
- Technical content: It reduces non-matmul work and improves GPU occupancy.
- Principle: Efficient inference and training depend on low-level implementation choices.

### P098 Efficient Memory Management for LLM Serving with PagedAttention (vLLM, 2023)
- Source: https://arxiv.org/abs/2309.06180
- Area: serving systems.
- Overview: PagedAttention made LLM serving more memory efficient.
- Technical content: It manages KV cache blocks similarly to virtual memory pages.
- Principle: Serving throughput depends heavily on KV-cache management.

### P099 Fast Inference from Transformers via Speculative Decoding (2022)
- Source: https://arxiv.org/abs/2211.17192
- Area: inference acceleration.
- Overview: Speculative decoding speeds generation by using a small draft model.
- Technical content: A target model verifies several draft tokens in parallel.
- Principle: Exact output distributions can be preserved while reducing latency.

### P100 A Survey on Efficient Inference for Large Language Models (2024)
- Source: https://arxiv.org/abs/2404.14294
- Area: inference survey.
- Overview: This survey categorizes methods for reducing LLM inference cost.
- Technical content: It covers quantization, pruning, distillation, batching, attention optimization, and decoding.
- Principle: Deployment quality depends on latency, memory, and cost, not only benchmark accuracy.

### P101 MMLU: Measuring Massive Multitask Language Understanding (2020)
- Source: https://arxiv.org/abs/2009.03300
- Area: benchmark.
- Overview: MMLU evaluates broad knowledge and problem-solving across many subjects.
- Technical content: It uses multiple-choice questions spanning STEM, humanities, social science, and more.
- Principle: General LLM evaluation needs broad domain coverage.

### P102 HellaSwag: Can a Machine Really Finish Your Sentence? (2019)
- Source: https://arxiv.org/abs/1905.07830
- Area: commonsense evaluation.
- Overview: HellaSwag tests grounded commonsense continuation.
- Technical content: Adversarial filtering creates plausible but wrong endings.
- Principle: Language plausibility alone is not enough for commonsense reasoning.

### P103 AI2 Reasoning Challenge (ARC, 2018)
- Source: https://arxiv.org/abs/1803.05457
- Area: science QA benchmark.
- Overview: ARC evaluates grade-school science question answering.
- Technical content: It includes challenge questions that require reasoning beyond retrieval.
- Principle: Benchmarks should include difficult examples where surface matching fails.

### P104 Measuring Coding Challenge Competence with APPS (2021)
- Source: https://arxiv.org/abs/2105.09938
- Area: code benchmark.
- Overview: APPS evaluates program synthesis using competitive programming style tasks.
- Technical content: Generated solutions are judged by hidden tests.
- Principle: Code generation should be evaluated by execution, not only text similarity.

### P105 AlphaCode: Competition-Level Code Generation with AlphaCode (2022)
- Source: https://arxiv.org/abs/2203.07814
- Area: code generation.
- Overview: AlphaCode used large-scale sampling and filtering for programming competitions.
- Technical content: It generates many candidate programs and selects a diverse subset.
- Principle: Search and selection are central to difficult code generation.

### P106 StarCoder: May the Source Be With You (2023)
- Source: https://arxiv.org/abs/2305.06161
- Area: open code models.
- Overview: StarCoder released open code LMs trained on permissively handled source data.
- Technical content: It includes data governance, filtering, and code-specific training.
- Principle: Code model releases need licensing and data transparency.

### P107 DeepSeek-Coder: When the Large Language Model Meets Programming (2024)
- Source: https://arxiv.org/abs/2401.14196
- Area: code LLM.
- Overview: DeepSeek-Coder focused on strong open code generation and completion.
- Technical content: It uses code-heavy pretraining and instruction tuning for programming tasks.
- Principle: Specialized corpora can produce high-performing code assistants.

### P108 Program of Thoughts Prompting (2022)
- Source: https://arxiv.org/abs/2211.12588
- Area: reasoning, tool use.
- Overview: Program of Thoughts asks models to express reasoning as executable code.
- Technical content: The model delegates computation to a program interpreter.
- Principle: Symbolic execution can reduce arithmetic and logic errors.

### P109 Program-Aided Language Models (PAL, 2022)
- Source: https://arxiv.org/abs/2211.10435
- Area: reasoning, code execution.
- Overview: PAL separates natural-language problem understanding from program execution.
- Technical content: A model writes Python snippets whose results answer math and symbolic tasks.
- Principle: Tools can make LLM reasoning more reliable.

### P110 ReAct: Synergizing Reasoning and Acting in Language Models (2022)
- Source: https://arxiv.org/abs/2210.03629
- Area: agents, tool use.
- Overview: ReAct interleaves reasoning traces with actions such as search or environment steps.
- Technical content: The model alternates thought, action, observation, and answer steps.
- Principle: Acting and reasoning should inform each other in grounded tasks.
