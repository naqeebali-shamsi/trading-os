//+------------------------------------------------------------------+
//| FileBridgeEA.mq5 — Autonomous Trading File IPC Bridge            |
//| Reads commands from MQL5/Files/trading-os/cmd_in.txt             |
//| Writes responses to MQL5/Files/trading-os/cmd_out.txt            |
//| Sends data to MQL5/Files/trading-os/data_out.txt (overwritten)   |
//| No TCP sockets — no compilation dependencies beyond basic MQL5   |
//+------------------------------------------------------------------+
#property copyright "Trading OS"
#property version   "2.00"
#property strict

//--- Input parameters
input string WorkspaceFolder = "trading-os";  // IPC subdirectory under MQL5/Files/
input uint   PollIntervalMs  = 500;           // File polling interval
input ulong  CommandMagic    = 261997;        // Magic number for this EA's orders

//--- File paths (virtual Wine paths resolved via MQL5/Files/)
string gcCmdInPath;
string gcCmdOutPath;
string gcDataOutPath;
string gcHeartbeatPath;
string gcLogPath;

//--- State
datetime gLastPoll   = 0;
datetime gHeartbeat  = 0;
bool     gFirstTick  = true;
ulong    gCmdCounter = 0;

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   // Build paths
   string filesPath = TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files\\";
   gcCmdInPath     = filesPath + WorkspaceFolder + "\\cmd_in.txt";
   gcCmdOutPath    = filesPath + WorkspaceFolder + "\\cmd_out.txt";
   gcDataOutPath   = filesPath + WorkspaceFolder + "\\data_out.txt";
   gcHeartbeatPath = filesPath + WorkspaceFolder + "\\heartbeat.txt";
   gcLogPath       = filesPath + WorkspaceFolder + "\\ea.log";

   // Ensure workspace directory exists on first tick
   gFirstTick = true;
   gHeartbeat = TimeLocal();

   // Verify this EA has trade permissions
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
     {
      WriteLog("INIT_WARN: Trade not allowed for this EA. Enable in Expert Properties and restart.");
     }

   WriteLog("FileBridgeEA initialized. Magic=" + IntegerToString(CommandMagic));
   WriteLog("Workspace=" + filesPath + WorkspaceFolder);

   EventSetMillisecondTimer(PollIntervalMs);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   WriteLog("FileBridgeEA deinitialized. Reason=" + IntegerToString(reason));
}

//+------------------------------------------------------------------+
//| Tick handler (writes data on each tick)                          |
//+------------------------------------------------------------------+
void OnTick()
{
   if(gFirstTick)
     {
      EnsureWorkspaceExists();
      gFirstTick = false;
     }

   // Fast path: write current data snapshot every ~5 seconds
   datetime now = TimeLocal();
   if(now - gLastPoll >= 5)
     {
      WriteDataSnapshot();
      WriteHeartbeat();
      gLastPoll = now;
     }
}

//+------------------------------------------------------------------+
//| Timer handler (checks for incoming commands)                     |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(gFirstTick)
      EnsureWorkspaceExists();

   CheckCommands();
}

//+------------------------------------------------------------------+
//| Ensure workspace directory exists                                |
//+------------------------------------------------------------------+
void EnsureWorkspaceExists()
{
   // MQL5 Files dir always exists, but we can't create subdirs easily
   // Just try to write a test file; directories created by Linux bridge
   string testFile = gcCmdInPath;
   ulong  handle;
   handle = FileOpen(testFile, FILE_WRITE|FILE_COMMON|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
      FileClose(handle);
}

//+------------------------------------------------------------------+
//| Write heartbeat timestamp to file                                |
//+------------------------------------------------------------------+
void WriteHeartbeat()
{
   int handle = FileOpen(gcHeartbeatPath, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
     {
      FileWriteString(handle, TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS) + "|alive");
      FileClose(handle);
     }
}

//+------------------------------------------------------------------+
//| Write current account + symbol data snapshot                     |
//+------------------------------------------------------------------+
void WriteDataSnapshot()
{
   int handle = FileOpen(gcDataOutPath, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      WriteLog("ERROR: Cannot open data_out.txt, err=" + IntegerToString(GetLastError()));
      return;
     }

   // Account info
   FileWriteString(handle, "ACCOUNT|");
   FileWriteString(handle, DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "|");
   FileWriteString(handle, DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + "|");
   FileWriteString(handle, DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + "|");
   FileWriteString(handle, DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + "|");
   FileWriteString(handle, DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2) + "|");
   FileWriteString(handle, IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "|");
   FileWriteString(handle, AccountInfoString(ACCOUNT_SERVER) + "\n");

   // Open positions
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
        {
         if(PositionGetInteger(POSITION_MAGIC) == CommandMagic)
           {
            FileWriteString(handle, "POSITION|");
            FileWriteString(handle, IntegerToString(ticket) + "|");
            FileWriteString(handle, PositionGetString(POSITION_SYMBOL) + "|");
            FileWriteString(handle, DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + "|");
            FileWriteString(handle, ((PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "buy" : "sell") + "|");
            FileWriteString(handle, DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) + "|");
            FileWriteString(handle, DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 5) + "|");
            FileWriteString(handle, DoubleToString(PositionGetDouble(POSITION_SL), 5) + "|");
            FileWriteString(handle, DoubleToString(PositionGetDouble(POSITION_TP), 5) + "|");
            FileWriteString(handle, DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + "|");
            FileWriteString(handle, TimeToString(PositionGetInteger(POSITION_TIME)) + "\n");
           }
        }
     }

   // Current symbol tick
   string sym = Symbol();
   MqlTick tick;
   if(SymbolInfoTick(sym, tick))
     {
      FileWriteString(handle, "TICK|" + sym + "|");
      FileWriteString(handle, DoubleToString(tick.bid, 5) + "|");
      FileWriteString(handle, DoubleToString(tick.ask, 5) + "|");
      FileWriteString(handle, DoubleToString(tick.last, 5) + "|");
      FileWriteString(handle, IntegerToString(tick.time) + "\n");
     }

   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Check for incoming commands                                      |
//+------------------------------------------------------------------+
void CheckCommands()
{
   int handle = FileOpen(gcCmdInPath, FILE_READ|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return;  // Nothing to do

   string raw = FileReadString(handle);
   FileClose(handle);

   // Immediately clear the command file (atomic: rewrite empty)
   int clearHandle = FileOpen(gcCmdInPath, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(clearHandle != INVALID_HANDLE)
      FileClose(clearHandle);

   if(StringLen(raw) < 3)
      return;

   // Parse command: CMD|id|param1|param2...
   string parts[];
   int n = StringSplit(raw, '|', parts);
   if(n < 2)
     {
      WriteResponse("ERROR|invalid_format");
      return;
     }

   string cmd  = parts[0];
   string cid  = parts[1];  // command correlation id
   gCmdCounter++;

   if(cmd == "PING")
      WriteResponse(cid + "|OK|pong");
   else if(cmd == "BALANCE")
     {
      WriteResponse(cid + "|OK|balance=" +
        DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "," +
        "equity=" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + "," +
        "margin=" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + "," +
        "free=" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2));
     }
   else if(cmd == "SYMBOLS")
      HandleSymbols(cid);
   else if(cmd == "RATES")
      HandleRates(cid, parts, n);
   else if(cmd == "ORDER")
      HandleOrder(cid, parts, n);
   else if(cmd == "CLOSE")
      HandleClose(cid, parts, n);
   else if(cmd == "CLOSE_ALL")
      HandleCloseAll(cid);
   else if(cmd == "MODIFY")
      HandleModify(cid, parts, n);
   else
      WriteResponse(cid + "|ERROR|unknown_cmd:" + cmd);
}

//+------------------------------------------------------------------+
//| ORDER command handler                                            |
//| ORDER|cid|symbol|volume|buy|sl|tp|comment                        |
//+------------------------------------------------------------------+
void HandleOrder(string cid, string &parts[], int n)
{
   if(n < 5)
     {
      WriteResponse(cid + "|ERROR|missing_params");
      return;
     }

   string symbol   = parts[2];
   double volume   = StringToDouble(parts[3]);
   bool   isBuy    = (parts[4] == "buy");
   double sl       = (n > 5) ? StringToDouble(parts[5]) : 0.0;
   double tp       = (n > 6) ? StringToDouble(parts[6]) : 0.0;
   string comment  = (n > 7) ? parts[7] : "os_order";

   // Symbol validation
   if(!SymbolSelect(symbol, true))
     {
      WriteResponse(cid + "|ERROR|symbol_unavailable:" + symbol);
      return;
     }

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
     {
      WriteResponse(cid + "|ERROR|no_tick:" + symbol);
      return;
     }

   // Volume validation
   double minVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double stepVol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(volume < minVol || volume > maxVol)
     {
      WriteResponse(cid + "|ERROR|volume_out_of_range:" + DoubleToString(volume, 2));
      return;
     }

   // Round to step
   volume = MathFloor(volume / stepVol) * stepVol;

   ENUM_ORDER_TYPE orderType = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = isBuy ? tick.ask : tick.bid;

   MqlTradeRequest request = {};
   request.action       = TRADE_ACTION_DEAL;
   request.symbol       = symbol;
   request.volume       = volume;
   request.type         = orderType;
   request.price        = price;
   request.deviation    = 10;
   request.magic        = CommandMagic;
   request.comment      = comment;
   request.sl           = (sl > 0.00001) ? sl : 0;
   request.tp           = (tp > 0.00001) ? tp : 0;
   request.type_filling = GetFillingMode(symbol);

   MqlTradeResult result = {};
   bool sent = OrderSend(request, result);

   if(!sent || result.retcode < 10000)
      WriteResponse(cid + "|ERROR|order_failed|retcode=" + IntegerToString(result.retcode));
   else
      WriteResponse(cid + "|OK|ticket=" + IntegerToString(result.order) +
         "|price=" + DoubleToString(result.price, 5) +
         "|volume=" + DoubleToString(volume, 2));
}

//+------------------------------------------------------------------+
//| CLOSE command handler                                            |
//| CLOSE|cid|ticket                                                 |
//+------------------------------------------------------------------+
void HandleClose(string cid, string &parts[], int n)
{
   if(n < 3)
     {
      WriteResponse(cid + "|ERROR|missing_ticket");
      return;
     }

   ulong ticket = StringToInteger(parts[2]);
   if(ticket == 0 || !PositionSelectByTicket(ticket))
     {
      WriteResponse(cid + "|ERROR|position_not_found:" + IntegerToString(ticket));
      return;
     }

   string symbol = PositionGetString(POSITION_SYMBOL);
   long posType  = PositionGetInteger(POSITION_TYPE);
   double volume = PositionGetDouble(POSITION_VOLUME);

   MqlTick tick;
   SymbolInfoTick(symbol, tick);

   MqlTradeRequest request = {};
   request.action       = TRADE_ACTION_DEAL;
   request.position     = ticket;
   request.symbol       = symbol;
   request.volume       = volume;
   request.type         = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.price        = (posType == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
   request.deviation    = 10;
   request.magic        = CommandMagic;
   request.type_filling = GetFillingMode(symbol);

   MqlTradeResult result = {};
   bool sent = OrderSend(request, result);

   if(!sent || result.retcode < 10000)
      WriteResponse(cid + "|ERROR|close_failed|retcode=" + IntegerToString(result.retcode));
   else
      WriteResponse(cid + "|OK|closed_ticket=" + IntegerToString(ticket) +
         "|price=" + DoubleToString(result.price, 5));
}

//+------------------------------------------------------------------+
//| CLOSE_ALL command handler                                        |
//+------------------------------------------------------------------+
void HandleCloseAll(string cid)
{
   int closed = 0;
   int errs   = 0;
   int total  = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
        {
         if(PositionGetInteger(POSITION_MAGIC) != CommandMagic)
            continue; // Don't touch manually placed orders
         string symbol = PositionGetString(POSITION_SYMBOL);
         long posType  = PositionGetInteger(POSITION_TYPE);
         double volume = PositionGetDouble(POSITION_VOLUME);
         MqlTick tick;
         SymbolInfoTick(symbol, tick);
         MqlTradeRequest req = {};
         req.action       = TRADE_ACTION_DEAL;
         req.position     = ticket;
         req.symbol       = symbol;
         req.volume       = volume;
         req.type         = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
         req.price        = (posType == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
         req.deviation    = 10;
         req.magic        = CommandMagic;
         req.type_filling = GetFillingMode(symbol);
         MqlTradeResult res = {};
         if(OrderSend(req, res) && res.retcode >= 10000)
            closed++;
         else
            errs++;
        }
     }
   WriteResponse(cid + "|OK|closed=" + IntegerToString(closed) + "|errors=" + IntegerToString(errs));
}

//+------------------------------------------------------------------+
//| MODIFY command handler                                           |
//| MODIFY|cid|ticket|sl|tp                                          |
//+------------------------------------------------------------------+
void HandleModify(string cid, string &parts[], int n)
{
   if(n < 4)
     {
      WriteResponse(cid + "|ERROR|missing_params");
      return;
     }
   ulong ticket = StringToInteger(parts[2]);
   double newSL = StringToDouble(parts[3]);
   double newTP = (n > 4) ? StringToDouble(parts[4]) : 0;

   if(!PositionSelectByTicket(ticket))
     {
      WriteResponse(cid + "|ERROR|position_not_found");
      return;
     }

   string symbol = PositionGetString(POSITION_SYMBOL);

   MqlTradeRequest req = {};
   req.action    = TRADE_ACTION_SLTP;
   req.position  = ticket;
   req.symbol    = symbol;
   req.sl        = (newSL > 0.00001) ? newSL : PositionGetDouble(POSITION_SL);
   req.tp        = (newTP > 0.00001) ? newTP : PositionGetDouble(POSITION_TP);

   MqlTradeResult res = {};
   if(OrderSend(req, res) && res.retcode >= 10000)
      WriteResponse(cid + "|OK|modified=" + IntegerToString(ticket));
   else
      WriteResponse(cid + "|ERROR|modify_failed|retcode=" + IntegerToString(res.retcode));
}

//+------------------------------------------------------------------+
//| SYMBOLS command handler                                          |
//+------------------------------------------------------------------+
void HandleSymbols(string cid)
{
   string out = "";
   int total = SymbolsTotal(true);
   for(int i = 0; i < MathMin(total, 500); i++)
     {
      string sym = SymbolName(i, true);
      if(StringLen(sym) == 0) break;
      if(StringLen(out) > 0) out += ",";
      out += sym;
     }
   WriteResponse(cid + "|OK|symbols=" + out);
}

//+------------------------------------------------------------------+
//| RATES command handler — return OHLCV for a symbol                |
//| RATES|cid|symbol|timeframe|count                                |
//+------------------------------------------------------------------+
void HandleRates(string cid, string &parts[], int n)
{
   if(n < 5)
     {
      WriteResponse(cid + "|ERROR|missing_params");
      return;
     }
   string symbol = parts[2];
   ENUM_TIMEFRAMES tf = (ENUM_TIMEFRAMES)StringToInteger(parts[3]);
   int count = (int)StringToInteger(parts[4]);
   if(count < 1 || count > 1000) count = 10;

   MqlRates rates[];
   int copied = CopyRates(symbol, tf, 0, count, rates);
   if(copied < 1)
     {
      WriteResponse(cid + "|ERROR|rates_copy_failed");
      return;
     }

   string out = "";
   for(int i = 0; i < copied; i++)
     {
      if(StringLen(out) > 0) out += ";";
      out += IntegerToString(rates[i].time) + ",";
      out += DoubleToString(rates[i].open, 5) + ",";
      out += DoubleToString(rates[i].high, 5) + ",";
      out += DoubleToString(rates[i].low, 5) + ",";
      out += DoubleToString(rates[i].close, 5) + ",";
      out += IntegerToString(rates[i].tick_volume);
     }
   WriteResponse(cid + "|OK|count=" + IntegerToString(copied) + "|rates=" + out);
}

//+------------------------------------------------------------------+
//| Write response to cmd_out.txt                                    |
//+------------------------------------------------------------------+
void WriteResponse(string text)
{
   int handle = FileOpen(gcCmdOutPath, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
     {
      FileWriteString(handle, text + "\n");
      FileClose(handle);
     }
   else
     {
      // Fallback: update the heartbeat line with error
      WriteLog("FATAL: Cannot write cmd_out.txt");
     }
}

//+------------------------------------------------------------------+
//| Write to internal EA log                                         |
//+------------------------------------------------------------------+
void WriteLog(string text)
{
   int handle = FileOpen(gcLogPath, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle != INVALID_HANDLE)
     {
      FileWriteString(handle, TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS) + " " + text + "\n");
      FileClose(handle);
     }
   Print(text);
}

//+------------------------------------------------------------------+
//| Get correct order filling mode for a symbol                      |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode(string symbol)
{
   uint filling = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}
//+------------------------------------------------------------------+
