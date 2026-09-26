const fs = require('fs');
const path = require('path');

// --- Question Patterns ---
// Raw:  [教育部] 物理-电学-欧姆定律: 一段导体的电阻为10欧...
// After stripping [xxx]: 物理-电学-欧姆定律: 一段导体的电阻为10欧...
const QUESTION_REGEX = /^(.*?)[:：]\s*(.+)$/;

// --- Session End Patterns ---
// Timeout: [教育部] 回答时间结束。正确答案是：末
const TIMEOUT_REGEX = /^\[(.*?)\]\s*回答时间结束[。.]\s*正确答案是[：:]\s*(.+)$/;
// Winner: [教育部] pECHOq 在 3.02 秒后获胜！答案是：12
const WINNER_REGEX = /^\[(.*?)\]\s*(.*?)\s*在\s*[\d.]+\s*秒后获胜[！!]\s*答案是[：:]\s*(.+)$/;
// No winner: [教育部] 无人答对！答案是: 递减
const NO_WINNER_REGEX = /^\[(.*?)\]\s*(?:无人|没有人)答对[。.！!]?\s*(?:正确)?答案是[：:]\s*(.+)$/;

class QuizHandler {
    constructor(bot, ChatLog, Log, Config) {
        this.bot = bot;
        this.ChatLog = ChatLog;
        this.Log = Log;
        this.config = Config.quiz || {};
        this.llmConfig = Config.llm || {};

        // Quiz session state
        this.session = null; // { question, category, startTime, timeout }

        // Load question bank
        this.questionBankPath = path.resolve(this.config.questionBank || 'questionBank.json');
        this.questionBank = this._loadQuestionBank();
    }

    // --- Question Bank ---
    _loadQuestionBank() {
        try {
            if (fs.existsSync(this.questionBankPath)) {
                const data = JSON.parse(fs.readFileSync(this.questionBankPath, 'utf8'));
                this.Log.info(`Question bank loaded: ${Object.keys(data.questions || {}).length} questions`);
                return data;
            }
        } catch (e) {
            this.Log.warn(`Failed to load question bank: ${e.message}`);
        }
        return { questions: {} };
    }

    _saveQuestionBank() {
        try {
            fs.writeFileSync(this.questionBankPath, JSON.stringify(this.questionBank, null, 2), 'utf8');
        } catch (e) {
            this.Log.warn(`Failed to save question bank: ${e.message}`);
        }
    }

    _lookupQuestion(question) {
        return this.questionBank.questions[question] || null;
    }

    _saveQuestion(question, answer) {
        this.questionBank.questions[question] = {
            answer,
            timestamp: Date.now(),
        };
        this._saveQuestionBank();
        this.Log.info(`Saved to question bank: "${question}" => "${answer}"`);
    }

    // --- Question detection (ActionBar and chat "[教育部] 新题目: ...") ---
    _detectQuestion(text) {
        if (!this.config.enabled) return;

        // Remove a leading [xxx] prefix and an optional "新题目:" marker.
        const stripped = String(text)
            .replace(/^\[.*?\]\s*/, '')
            .replace(/^新题目\s*[:：]?\s*/, '');
        const match = stripped.match(QUESTION_REGEX);

        if (!match) return;

        const [, category, questionText] = match;
        const fullQuestion = `${category}: ${questionText}`;

        // Skip if same question is already active
        if (this.session && this.session.question === fullQuestion) return;

        // Start new quiz session
        this.Log.info(`Quiz detected: [${category}] ${fullQuestion}`);
        this._startSession(fullQuestion);
    }

    // --- ActionBar Processing ---
    onActionBar(text) {
        this._detectQuestion(text);
    }

    async _startSession(question) {
        // End previous session if exists
        if (this.session) {
            this._endSession();
        }

        this.session = {
            question,
            startTime: Date.now(),
            answered: false,
        };

        this.Log.info(`Quiz session started: "${question}"`);

        // Check question bank first
        const cached = this._lookupQuestion(question);
        if (cached) {
            this.Log.info(`Question bank hit: "${question}" => "${cached.answer}"`);
            this._sendAnswer(cached.answer);
            return;
        }

        // Call LLM API
        this._queryLLM(question);
    }

    // --- Answer extraction (always strip the model's thinking) ---
    _extractAnswer(data) {
        const msg = (data && data.choices && data.choices[0] && data.choices[0].message) || {};
        const stripThink = (s) => String(s == null ? '' : s)
            .replace(/<think[\s\S]*?<\/think>/gi, '')
            .replace(/<reasoning[\s\S]*?<\/reasoning>/gi, '')
            .trim();

        // Prefer the final answer in `content`; reasoning_content is a fallback.
        let answer = stripThink(msg.content);
        if (!answer) {
            const rc = stripThink(msg.reasoning_content);
            const lines = rc.split('\n').map((s) => s.trim()).filter(Boolean);
            answer = lines.length ? lines[lines.length - 1] : rc;
        }
        // Multi-line replies: keep the last non-empty line.
        if (answer.includes('\n')) {
            const lines = answer.split('\n').map((s) => s.trim()).filter(Boolean);
            if (lines.length) answer = lines[lines.length - 1];
        }
        // If reasoning leaked into the text, pull the last "answer"-ish token.
        if (answer.length > 20) {
            const re = /(?:所以输出|最终答案|正确答案是|正确答案|答案是|答案|应填|输出)\s*[:：]?\s*["'“”]?([^\s。，,；;.!！?？"'“”]{1,24})["'“”]?/g;
            let m;
            let last = null;
            while ((m = re.exec(answer)) !== null) last = m[1];
            if (last) answer = last;
        }
        // Cleanup: leading labels and surrounding quotes / trailing periods.
        answer = answer.replace(/^(?:最终答案|正确答案是|正确答案|答案是|答案|所以输出|所以|输出|应填|答)\s*[:：]?\s*/, '').trim();
        answer = answer.replace(/^["'“”‘’`]+|["'“”‘’`]+$/g, '').replace(/[。.]+$/, '').trim();
        return answer;
    }

    // --- LLM API ---
    async _queryLLM(question) {
        if (!this.llmConfig.endpoint || !this.llmConfig.apiKey) {
            this.Log.warn("LLM API not configured, skipping answer");
            return;
        }

        try {
            const systemPrompt = `你是答题机器人。严格遵守以下规则，违反任何一条都是错误：

规则：
1. 禁止输出任何句子、解释、标点、引号、空格以外的字符
2. 选择题：只输出选项字母，如 a 或 b 或 c 或 d，不要输出括号和点
3. 填空题：只输出空格处应填的词语，不要输出整句话
4. 判断题：只输出"对"或"错"

示例：
题目：月野兔的名言是"代表___消灭你"。→ 答案：月亮
题目：Who are you? A. AI B. assistant → 答案：a
题目：1+1=? → 答案：2
题目：地球是平的。→ 答案：错

禁止输出的格式：
✗ 月野兔的名言是"代表月亮消灭你"。
✗ 答案是：月亮
✗ 月亮。
✗ A. AI

只输出答案本身，其他什么都不要。`;

            const response = await fetch(this.llmConfig.endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.llmConfig.apiKey}`,
                    ...(this.llmConfig.headers || {}),
                },
                body: JSON.stringify({
                    model: this.llmConfig.model,
                    messages: [
                        { role: 'system', content: systemPrompt },
                        { role: 'user', content: question },
                    ],
                    temperature: 0,
                    reasoning_effort: 'low',
                    max_tokens: 5120,
                }),
            });

            if (!response.ok) {
                throw new Error(`LLM API error: ${response.status}`);
            }

            const data = await response.json();
            this.Log.info(`LLM raw response: ${JSON.stringify(data).substring(0, 500)}`);
            // Use the final answer (content); reasoning_content is only a
            // fallback and the thinking is always stripped.
            let answer = this._extractAnswer(data);

            if (!answer) {
                this.Log.warn("LLM returned empty answer");
                return;
            }

            this.Log.info(`LLM answer: "${answer}"`);

            // Save to question bank
            this._saveQuestion(question, answer);

            // Send answer after minimum delay
            this._sendAnswer(answer);
        } catch (e) {
            this.Log.error(`LLM query failed: ${e.message}`);
        }
    }

    // --- Send Answer ---
    _sendAnswer(answer) {
        if (!this.session || this.session.answered) return;

        const elapsed = Date.now() - this.session.startTime;
        const minDelay = this.config.minDelay || 3500;
        const remaining = Math.max(0, minDelay - elapsed);

        this.Log.info(`Sending answer in ${remaining}ms: "${answer}"`);

        setTimeout(() => {
            if (this.session && !this.session.answered) {
                this.bot.chat(answer);
                this.session.answered = true;
                this.Log.info(`Answer sent: "${answer}"`);
            }
        }, remaining);
    }

    // --- Chat Processing (Session End + chat-bar questions) ---
    onChat(rawMessage) {
        const message = rawMessage.replace(/§[0-9a-fk-or]/gi, ''); // Strip color codes

        // Session end (timeout / winner / no winner) while a session is active.
        if (this.session) {
            const timeoutMatch = message.match(TIMEOUT_REGEX);
            const winnerMatch = message.match(WINNER_REGEX);
            const noWinnerMatch = message.match(NO_WINNER_REGEX);
            if (timeoutMatch) {
                const [, source, correctAnswer] = timeoutMatch;
                this.Log.info(`Quiz timeout. Correct answer: "${correctAnswer}"`);
                this._saveQuestion(this.session.question, correctAnswer.trim());
                this._endSession();
                return;
            }
            if (winnerMatch) {
                const [, source, player, correctAnswer] = winnerMatch;
                this.Log.info(`Quiz won by ${player}. Answer: "${correctAnswer}"`);
                this._saveQuestion(this.session.question, correctAnswer.trim());
                this._endSession();
                return;
            }
            if (noWinnerMatch) {
                const [, source, correctAnswer] = noWinnerMatch;
                this.Log.info(`Quiz no winner. Correct answer: "${correctAnswer}"`);
                this._saveQuestion(this.session.question, correctAnswer.trim());
                this._endSession();
                return;
            }
        }

        // Questions can also appear in the chat bar as "[教育部] ...". Answer
        // any [教育部] message that is not a settlement/scoring line.
        if (!this.config.enabled) return;
        if (message.indexOf('教育部') < 0) return;
        if (/回答时间结束|正确答案|答题结束|获胜|无人答对|没有人答对|答对了|货币/.test(message)) return;
        this._detectQuestion(message);
    }

    _endSession() {
        if (this.session) {
            this.Log.info(`Quiz session ended: "${this.session.question}"`);
            this.session = null;
        }
    }
}

module.exports = { QuizHandler };
