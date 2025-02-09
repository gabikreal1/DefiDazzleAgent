require('dotenv').config();
const express = require('express');
const { OpenAI, ChatOpenAI } = require('@langchain/openai');
const { z } = require('zod');

const app = express();

// Add this middleware before your routes
app.use(express.json());  // Parse JSON bodies
app.use(express.urlencoded({ extended: true }));  // Parse URL-encoded bodies

// Define the allowed tickers to be used in the command context.
const allowedTickers = [
  "ALOT", "ARENA", "BTC.b", "COQ", "EURC", "GEC", "GMX",
  "KIMBO", "LOST", "MEOW", "NOCHILL", "PLSR", "QI", "sAVAX",
  "SHRAP", "STL", "TECH", "TRUMP", "USDC", "USDt", "WETH.e", "WAVAX"
];

// Define the allowed operations that a command can represent.
const allowedOperations = ["swap",  "send", "getBalance"];

/*
  We want to represent a swap command with two parts:
  
  1. SwapIntent:
      - baseAmount: string  // numeric amount for the swap, represented as a string.
  
  2. PairInfo:
      - base: string        // ticker to swap from; must be an allowed ticker.
      - quote: string       // ticker to swap to; must be an allowed ticker.
*/

// Define the Zod schema for structured output
const SwapCommandSchema = z.object({
  pair: z.object({
    base: z.string().refine(value => allowedTickers.includes(value), {
      message: `Base ticker must be one of: ${allowedTickers.join(", ")}`
    }),
    quote: z.string().refine(value => allowedTickers.includes(value), {
      message: `Quote ticker must be one of: ${allowedTickers.join(", ")}`
    }),
  }),
  intent: z.object({
    baseAmount: z.string().regex(/^\d+(\.\d+)?$/, "baseAmount must be a valid numeric string")
  })
});

// Define the command input schema
const CommandInputSchema = z.object({
  command: z.string({
    required_error: "Command is required",
    invalid_type_error: "Command must be a string"
  }).min(1, "Command cannot be empty")
});

// A basic test endpoint to verify OpenAI integration.
app.get('/api/test', async (req, res) => {
  console.log('[/api/test] Received request');
  try {
    const model = new OpenAI({
      openAIApiKey: process.env.OPENAI_API_KEY,
      temperature: 0.7
    });
    console.log('[/api/test] Initialized OpenAI model');

    const prompt = "Provide a friendly greeting to the user.";
    console.log('[/api/test] Sending prompt to OpenAI:', prompt);
    const result = await model.call(prompt);
    console.log('[/api/test] Received response from OpenAI:', result);

    res.json({ message: "OpenAI endpoint is working!", result });
  } catch (error) {
    console.error("[/api/test] Error:", error);
    res.status(500).json({ error: error.toString() });
  }
});

// Change swap from GET to POST
app.post('/api/swap', async (req, res) => {
  console.log('[/api/swap] Received request with body:', req.body);
  try {
    // Validate the input first
    const validatedInput = CommandInputSchema.safeParse(req.body);
    if (!validatedInput.success) {
      console.error('[/api/swap] Input validation error:', validatedInput.error);
      return res.status(400).json({ 
        error: "Invalid input", 
        details: validatedInput.error.errors 
      });
    }

    const { command } = validatedInput.data;
    const tickerList = allowedTickers.join(", ");
    console.log('[/api/swap] Using tickers:', tickerList);

    // Initialize OpenAI with structured output
    const structuredModel = new ChatOpenAI({
      openAIApiKey: process.env.OPENAI_API_KEY,
      temperature: 0.1,
      modelName: "gpt-4-1106-preview",  // Using a model that supports JSON mode
    }).withStructuredOutput(SwapCommandSchema, {
      name: "parse_swap_command",
      description: "Parse a swap command into base/quote pair and amount",
    });
    
    console.log('[/api/swap] Initialized OpenAI model with structured output');

    const result = await structuredModel.invoke([
      {
        role: "system",
        content: `You are a swap command parser. Parse commands using these allowed tickers: ${tickerList}`
      },
      {
        role: "user",
        content: command
      }
    ]);

    console.log('[/api/swap] Received parsed result:', result);

    res.json({ 
      message: "Swap command parsed successfully", 
      result: result 
    });

  } catch (error) {
    console.error("[/api/swap] Error:", error);
    res.status(500).json({ error: error.toString() });
  }
});

// Add startup logging
const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`[Server] Started successfully on http://localhost:${PORT}`);
  console.log('[Server] Available tickers:', allowedTickers);
  console.log('[Server] Available operations:', allowedOperations);
});
