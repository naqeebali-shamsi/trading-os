//+------------------------------------------------------------------+
//|                                   FileBridgeEA_MultiSymbol.mq5  |
//|         Trading OS -- Multi-Symbol File IPC Bridge    |
//|         Version: 6.00  --  One EA per chart, one symbol each    |
//+------------------------------------------------------------------+
#property copyright "Trading OS"
#property link      "https://trading-os.local"
#property version   "6.00"
#property strict

// === CONFIG ===
// Junction: Terminal\Common\Files\trading-os  ->  E:\GROWTH\trading-os\ipc\
// This EA writes to: trading-os/chart_<SYMBOL>/
input string InpIpcPrefix = "trading-os/";
input int    InpMagic     = 133742;
input double InpMaxSlippage = 3.0;
input int    InpTimerSec  = 3;
input bool   InpVerbose   = false;

string g_chartDir;      // e.g. "trading-os/chart_EURUSD/"
string g_cmdFile;
string g_respFile;
string g_hbFile;
string g_tickFile;
string g_chartLabel;    // e.g. "chart_EURUSD"

static bool g_tradeAllowed = false;

//+------------------------------------------------------------------+
int OnInit()
{
   string sym = _Symbol;
   g_chartLabel = "chart_" + sym;
   g_chartDir   = InpIpcPrefix + g_chartLabel + "/";

   g_cmdFile  = g_chartDir + "cmd_in.txt";
   g_respFile = g_chartDir + "cmd_out.txt";
   g_hbFile   = g_chartDir + "heartbeat.txt";
   g_tickFile = g_chartDir + "tick.txt";

   g_tradeAllowed = MQLInfoInteger(MQL_TRADE_ALLOWED) != 0;
   if(!g_tradeAllowed)
   {
      Alert("[FileBridgeEA] Trade not allowed on ", sym,
            ". Enable 'Allow Algo Trading' in EA properties.");
   }

   EventSetTimer(InpTimerSec);
   WriteHeartbeat();
   WriteTick();

   if(InpVerbose)
      Print("[FileBridgeEA] Initialised for ", sym, " IPC dir: ", g_chartDir);

   return(INIT_SUCCEEDED);
}
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   if(InpVerbose)
      Print("[FileBridgeEA] Shutdown ", _Symbol, ". Reason: ", reason);
}
//+------------------------------------------------------------------+
void OnTick()
{
   // OnTick reserved for future indicator logic.
   // All I/O happens in OnTimer to avoid file races.
}
//+------------------------------------------------------------------+
void OnTimer()
{
   WriteHeartbeat();
   WriteTick();
   ReadCommand();
}
//+------------------------------------------------------------------+
void WriteHeartbeat()
{
   int h = FileOpen(g_hbFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h != INVALID_HANDLE)
   {
      string unix = IntegerToString((long)TimeLocal());
      FileWriteString(h, unix + "|alive\n");
      FileClose(h);
   }
}
//+------------------------------------------------------------------+
void WriteTick()
{
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t)) return;

   int h = FileOpen(g_tickFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h != INVALID_HANDLE)
   {
      string line = _Symbol + "," + DoubleToString(t.bid, _Digits)
                  + "," + DoubleToString(t.ask, _Digits)
                  + "," + IntegerToString(t.time);
      FileWriteString(h, line + "\n");
      FileClose(h);
   }
}
//+------------------------------------------------------------------+
int GetFillingMode(string sym)
{
   uint filling = (uint)SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK) return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}
//+------------------------------------------------------------------+
void ReadCommand()
{
   if(!FileIsExist(g_cmdFile, FILE_COMMON)) return;

   int h = FileOpen(g_cmdFile, FILE_READ|FILE_TXT|FILE_COMMON);
   if(h == INVALID_HANDLE) return;

   string line = FileReadString(h);
   FileClose(h);

   if(!FileDelete(g_cmdFile, FILE_COMMON))
   {
      if(InpVerbose)
         Print("[FileBridgeEA] FileDelete failed on ", _Symbol, ", skipping command.");
      return;
   }

   if(StringLen(line) < 3) return;
   if(InpVerbose) Print("[FileBridgeEA] CMD [", _Symbol, "]: ", line);

   string parts[];
   int n = StringSplit(line, StringGetCharacter(",", 0), parts);
   if(n < 1) return;

   string action = parts[0];

   if(action == "ORDER")
   {
      if(n < 7)
      {
         WriteErrorResponse("invalid_command", "ORDER requires 7 CSV fields");
         return;
      }
      string sym  = parts[1];
      string side = parts[2];
      double vol  = StringToDouble(parts[3]);
      double sl   = StringToDouble(parts[4]);
      double tp   = StringToDouble(parts[5]);
      string oid  = parts[6];

      // Reject if command targets a different symbol
      if(sym != _Symbol)
      {
         WriteErrorResponse("symbol_mismatch",
            "Expected " + _Symbol + " but got " + sym, oid);
         return;
      }

      if(!SymbolSelect(sym, true))
      {
         WriteErrorResponse("invalid_symbol", sym, oid);
         return;
      }

      int symDigits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(symDigits <= 0)
      {
         WriteErrorResponse("invalid_symbol_digits", sym, oid);
         return;
      }

      ENUM_ORDER_TYPE otype = (side == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

      MqlTick tk;
      if(!SymbolInfoTick(sym, tk))
      {
         WriteErrorResponse("no_tick", sym, oid);
         return;
      }

      MqlTradeRequest req = {};
      req.action       = TRADE_ACTION_DEAL;
      req.symbol       = sym;
      req.volume       = vol;
      req.type         = otype;
      req.price        = (otype == ORDER_TYPE_BUY) ? tk.ask : tk.bid;
      req.deviation    = (int)InpMaxSlippage;
      req.type_filling = GetFillingMode(sym);
      req.magic        = InpMagic;
      req.comment      = oid;
      if(sl > 0) req.sl = sl;
      if(tp > 0) req.tp = tp;

      MqlTradeResult res = {};
      bool ok = OrderSend(req, res);
      if(InpVerbose)
         Print("[FileBridgeEA] OrderSend retcode=", res.retcode,
               " deal=", res.deal, " price=", res.price);

      if(ok && res.retcode == TRADE_RETCODE_DONE)
      {
         WriteFillResponse(oid, res.retcode, res.price, sym, side, vol, symDigits);
      }
      else
      {
         if(res.retcode == 10004 || res.retcode == 10025 || res.retcode == 10026)
         {
            Sleep(250);
            SymbolInfoTick(sym, tk);
            req.price = (otype == ORDER_TYPE_BUY) ? tk.ask : tk.bid;
            ok = OrderSend(req, res);
         }
         if(ok && res.retcode == TRADE_RETCODE_DONE)
            WriteFillResponse(oid, res.retcode, res.price, sym, side, vol, symDigits);
         else
            WriteErrorResponse("order_failed", IntegerToString(res.retcode), oid);
      }
   }
   else if(action == "CLOSE_ALL")
   {
      int failCount = 0;
      string failTickets = "";
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
         string posSym = PositionGetString(POSITION_SYMBOL);
         if(posSym != _Symbol) continue;          // only close this chart's symbol

         MqlTick tk;
         if(!SymbolInfoTick(posSym, tk)) continue;

         MqlTradeRequest r = {};
         r.action       = TRADE_ACTION_DEAL;
         r.position     = ticket;
         r.symbol       = posSym;
         r.volume       = PositionGetDouble(POSITION_VOLUME);
         r.type         = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                           ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
         r.price        = (r.type == ORDER_TYPE_BUY) ? tk.ask : tk.bid;
         r.deviation    = (int)InpMaxSlippage;
         r.type_filling = GetFillingMode(posSym);
         r.magic        = InpMagic;
         MqlTradeResult res = {};
         if(!OrderSend(r, res) || res.retcode != TRADE_RETCODE_DONE)
         {
            failCount++;
            failTickets += IntegerToString((long)ticket) + ",";
         }
      }
      WriteCloseAllResponse(failCount, failTickets);
   }
   else if(action == "CLOSE")
   {
      // CLOSE,ticket,oid — single-position close on this chart symbol
      if(n < 3)
      {
         WriteErrorResponse("invalid_command", "CLOSE requires 3 CSV fields");
         return;
      }
      ulong ticket = (ulong)StringToInteger(parts[1]);
      string oid   = parts[2];

      if(!PositionSelectByTicket(ticket))
      {
         WriteErrorResponse("position_not_found", IntegerToString((long)ticket), oid);
         return;
      }
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
      {
         WriteErrorResponse("symbol_mismatch", _Symbol, oid);
         return;
      }

      MqlTick tk;
      if(!SymbolInfoTick(_Symbol, tk))
      {
         WriteErrorResponse("no_tick", _Symbol, oid);
         return;
      }

      MqlTradeRequest r = {};
      r.action       = TRADE_ACTION_DEAL;
      r.position     = ticket;
      r.symbol       = _Symbol;
      r.volume       = PositionGetDouble(POSITION_VOLUME);
      r.type         = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                        ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
      r.price        = (r.type == ORDER_TYPE_BUY) ? tk.ask : tk.bid;
      r.deviation    = (int)InpMaxSlippage;
      r.type_filling = GetFillingMode(_Symbol);
      r.magic        = InpMagic;
      MqlTradeResult res = {};
      if(OrderSend(r, res) && res.retcode == TRADE_RETCODE_DONE)
         WriteGenericAck(oid, "close_ok", IntegerToString((long)ticket));
      else
         WriteErrorResponse("close_failed", IntegerToString(res.retcode), oid);
   }
   else if(action == "PING")
   {
      string cid = (n >= 2) ? parts[1] : "";
      WritePingResponse(cid);
   }
   else if(action == "GET_STATUS" || action == "STATUS")
   {
      string cid = (n >= 2) ? parts[1] : "";
      WriteStatusResponse(cid);
   }
   else if(action == "GET_POSITIONS")
   {
      string cid = (n >= 2) ? parts[1] : "";
      WritePositionsResponse(cid);
   }
   else if(action == "GET_SYMBOL_INFO")
   {
      string cid = (n >= 2) ? parts[1] : "";
      string sym = (n >= 3) ? parts[2] : _Symbol;
      WriteSymbolInfoResponse(cid, sym);
   }
   else if(action == "SEARCH_SYMBOLS")
   {
      string cid = (n >= 2) ? parts[1] : "";
      string query = (n >= 3) ? parts[2] : "";
      int limit = (n >= 4) ? (int)StringToInteger(parts[3]) : 50;
      WriteSymbolSearchResponse(cid, query, limit);
   }
   else if(action == "MODIFY")
   {
      // MODIFY,ticket,sl,tp,oid
      if(n < 5)
      {
         WriteErrorResponse("invalid_command", "MODIFY requires 5 CSV fields");
         return;
      }
      ulong ticket = (ulong)StringToInteger(parts[1]);
      double nsl   = StringToDouble(parts[2]);
      double ntp   = StringToDouble(parts[3]);
      string oid   = parts[4];

      if(!PositionSelectByTicket(ticket))
      {
         WriteErrorResponse("position_not_found", IntegerToString((long)ticket), oid);
         return;
      }
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
      {
         WriteErrorResponse("symbol_mismatch", _Symbol, oid);
         return;
      }

      MqlTradeRequest req = {};
      req.action   = TRADE_ACTION_SLTP;
      req.position = ticket;
      req.symbol   = _Symbol;
      if(nsl > 0) req.sl = nsl;
      if(ntp > 0) req.tp = ntp;

      MqlTradeResult res = {};
      bool ok = OrderSend(req, res);
      if(ok && res.retcode == TRADE_RETCODE_DONE)
         WriteGenericAck(oid, "modify_ok", IntegerToString((long)ticket));
      else
         WriteErrorResponse("modify_failed", IntegerToString(res.retcode), oid);
   }
   else
   {
      WriteErrorResponse("unknown_action", action);
   }
}
//+------------------------------------------------------------------+
void WriteFillResponse(string oid, long retcode, double price, string sym,
                       string side, double vol, int digits)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh != INVALID_HANDLE)
   {
      string j = "{"
         "\"type\":\"fill\","
         "\"order_id\":\"" + oid + "\","
         "\"retcode\":" + IntegerToString(retcode) + ","
         "\"fill_price\":" + DoubleToString(price, digits) + ","
         "\"symbol\":\"" + sym + "\","
         "\"side\":\"" + side + "\","
         "\"qty\":" + DoubleToString(vol, 2) + "}\n";
      FileWriteString(wh, j);
      FileClose(wh);
   }
}
//+------------------------------------------------------------------+
void WriteErrorResponse(string err_type, string err_msg, string oid = "")
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh != INVALID_HANDLE)
   {
      string oidField = (StringLen(oid) > 0)
         ? "\"order_id\":\"" + oid + "\"," : "";
      string j = "{"
         "\"type\":\"error\","
         + oidField +
         "\"error_type\":\"" + err_type + "\","
         "\"message\":\"" + err_msg + "\"}\n";
      FileWriteString(wh, j);
      FileClose(wh);
   }
}
//+------------------------------------------------------------------+
void WriteCloseAllResponse(int failCount, string failTickets)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh != INVALID_HANDLE)
   {
      string j = "{"
         "\"type\":\"close_all_ack\","
         "\"fail_count\":" + IntegerToString(failCount) + ","
         "\"fail_tickets\":\"" + failTickets + "\"}\n";
      FileWriteString(wh, j);
      FileClose(wh);
   }
}
//+------------------------------------------------------------------+
void WriteGenericAck(string oid, string ack_type, string detail)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh != INVALID_HANDLE)
   {
      string j = "{"
         "\"type\":\"" + ack_type + "\","
         "\"order_id\":\"" + oid + "\","
         "\"detail\":\"" + detail + "\"}\n";
      FileWriteString(wh, j);
      FileClose(wh);
   }
}
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
string JsonEscape(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   return value;
}
//+------------------------------------------------------------------+
void WritePingResponse(string cid)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh != INVALID_HANDLE)
   {
      string cidField = (StringLen(cid) > 0) ? "\"cid\":\"" + JsonEscape(cid) + "\"," : "";
      string j = "{" + cidField + "\"type\":\"pong\",\"ok\":true,\"message\":\"pong\",\"symbol\":\""
         + JsonEscape(_Symbol) + "\",\"ts\":" + IntegerToString((long)TimeLocal()) + "}\n";
      FileWriteString(wh, j);
      FileClose(wh);
   }
}
//+------------------------------------------------------------------+
void WriteStatusResponse(string cid)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh != INVALID_HANDLE)
   {
      string cidField = (StringLen(cid) > 0) ? "\"cid\":\"" + JsonEscape(cid) + "\"," : "";
      string j = "{" + cidField
         + "\"type\":\"status\","
         + "\"ok\":true,"
         + "\"symbol\":\"" + JsonEscape(_Symbol) + "\","
         + "\"chart_label\":\"" + JsonEscape(g_chartLabel) + "\","
         + "\"trade_allowed\":" + (g_tradeAllowed ? "true" : "false") + ","
         + "\"positions_total\":" + IntegerToString(PositionsTotal()) + ","
         + "\"ts\":" + IntegerToString((long)TimeLocal()) + "}\n";
      FileWriteString(wh, j);
      FileClose(wh);
   }
}
//+------------------------------------------------------------------+
void WritePositionsResponse(string cid = "")
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh == INVALID_HANDLE) return;

   string cidField = (StringLen(cid) > 0) ? "\"cid\":\"" + JsonEscape(cid) + "\"," : "";
   string j = "{" + cidField + "\"type\":\"positions\",\"positions\":[";
   bool first = true;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      string side = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      string comment = PositionGetString(POSITION_COMMENT);
      int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
      if(digits <= 0) digits = 5;

      if(!first) j += ",";
      first = false;
      j += "{";
      j += "\"ticket\":\"" + IntegerToString((long)ticket) + "\",";
      j += "\"order_id\":\"" + JsonEscape(comment) + "\",";
      j += "\"symbol\":\"" + JsonEscape(_Symbol) + "\",";
      j += "\"side\":\"" + side + "\",";
      j += "\"qty\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
      j += "\"open_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), digits) + ",";
      j += "\"current_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), digits) + ",";
      j += "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), digits) + ",";
      j += "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), digits) + ",";
      j += "\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ",";
      j += "\"swap\":" + DoubleToString(PositionGetDouble(POSITION_SWAP), 2) + ",";
      j += "\"magic\":" + IntegerToString((long)PositionGetInteger(POSITION_MAGIC)) + ",";
      j += "\"ts\":" + IntegerToString((long)TimeLocal());
      j += "}";
   }
   j += "]}\n";
   FileWriteString(wh, j);
   FileClose(wh);
}
//+------------------------------------------------------------------+
string UpperCopy(string value)
{
   string out = value;
   StringToUpper(out);
   return out;
}
//+------------------------------------------------------------------+
bool TextMatchesQuery(string value, string query)
{
   if(StringLen(query) <= 0) return true;
   return (StringFind(UpperCopy(value), UpperCopy(query)) >= 0);
}
//+------------------------------------------------------------------+
void WriteSymbolInfoResponse(string cid, string sym)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh == INVALID_HANDLE) return;

   string cidField = (StringLen(cid) > 0) ? "\"cid\":\"" + JsonEscape(cid) + "\"," : "";
   bool selected = SymbolSelect(sym, true);
   MqlTick tk;
   bool hasTick = selected && SymbolInfoTick(sym, tk);
   int digits = selected ? (int)SymbolInfoInteger(sym, SYMBOL_DIGITS) : 0;
   double point = selected ? SymbolInfoDouble(sym, SYMBOL_POINT) : 0.0;
   double tickSize = selected ? SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE) : 0.0;
   double tickValue = selected ? SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE) : 0.0;
   double minLot = selected ? SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN) : 0.0;
   double maxLot = selected ? SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX) : 0.0;
   double lotStep = selected ? SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP) : 0.0;
   long tradeMode = selected ? SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) : -1;
   string currencyProfit = selected ? SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT) : "";
   string currencyBase = selected ? SymbolInfoString(sym, SYMBOL_CURRENCY_BASE) : "";
   string currencyMargin = selected ? SymbolInfoString(sym, SYMBOL_CURRENCY_MARGIN) : "";

   string j = "{" + cidField
      + "\"type\":\"symbol_info\","
      + "\"ok\":" + (selected ? "true" : "false") + ","
      + "\"symbol\":\"" + JsonEscape(sym) + "\","
      + "\"selected\":" + (selected ? "true" : "false") + ","
      + "\"has_tick\":" + (hasTick ? "true" : "false") + ","
      + "\"digits\":" + IntegerToString(digits) + ","
      + "\"point\":" + DoubleToString(point, 10) + ","
      + "\"tick_size\":" + DoubleToString(tickSize, 10) + ","
      + "\"tick_value\":" + DoubleToString(tickValue, 10) + ","
      + "\"min_lot\":" + DoubleToString(minLot, 2) + ","
      + "\"max_lot\":" + DoubleToString(maxLot, 2) + ","
      + "\"lot_step\":" + DoubleToString(lotStep, 2) + ","
      + "\"trade_mode\":" + IntegerToString((int)tradeMode) + ","
      + "\"currency_base\":\"" + JsonEscape(currencyBase) + "\","
      + "\"currency_profit\":\"" + JsonEscape(currencyProfit) + "\","
      + "\"currency_margin\":\"" + JsonEscape(currencyMargin) + "\","
      + "\"bid\":" + (hasTick ? DoubleToString(tk.bid, digits) : "0") + ","
      + "\"ask\":" + (hasTick ? DoubleToString(tk.ask, digits) : "0") + ","
      + "\"tick_time\":" + (hasTick ? IntegerToString((long)tk.time) : "0") + ","
      + "\"ts\":" + IntegerToString((long)TimeLocal()) + "}\n";
   FileWriteString(wh, j);
   FileClose(wh);
}
//+------------------------------------------------------------------+
void WriteSymbolSearchResponse(string cid, string query, int limit)
{
   int wh = FileOpen(g_respFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(wh == INVALID_HANDLE) return;
   if(limit <= 0 || limit > 200) limit = 50;

   string cidField = (StringLen(cid) > 0) ? "\"cid\":\"" + JsonEscape(cid) + "\"," : "";
   string j = "{" + cidField
      + "\"type\":\"symbol_search\","
      + "\"ok\":true,"
      + "\"query\":\"" + JsonEscape(query) + "\","
      + "\"symbols\":[";

   int total = SymbolsTotal(false);
   int count = 0;
   for(int i = 0; i < total && count < limit; i++)
   {
      string sym = SymbolName(i, false);
      string desc = SymbolInfoString(sym, SYMBOL_DESCRIPTION);
      string path = SymbolInfoString(sym, SYMBOL_PATH);
      if(!TextMatchesQuery(sym, query) && !TextMatchesQuery(desc, query) && !TextMatchesQuery(path, query)) continue;

      bool selected = SymbolSelect(sym, true);
      MqlTick tk;
      bool hasTick = selected && SymbolInfoTick(sym, tk);
      int digits = selected ? (int)SymbolInfoInteger(sym, SYMBOL_DIGITS) : 0;
      double minLot = selected ? SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN) : 0.0;
      double maxLot = selected ? SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX) : 0.0;
      double lotStep = selected ? SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP) : 0.0;
      long tradeMode = selected ? SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) : -1;

      if(count > 0) j += ",";
      j += "{";
      j += "\"symbol\":\"" + JsonEscape(sym) + "\",";
      j += "\"description\":\"" + JsonEscape(desc) + "\",";
      j += "\"path\":\"" + JsonEscape(path) + "\",";
      j += "\"selected\":" + (selected ? "true" : "false") + ",";
      j += "\"has_tick\":" + (hasTick ? "true" : "false") + ",";
      j += "\"digits\":" + IntegerToString(digits) + ",";
      j += "\"min_lot\":" + DoubleToString(minLot, 2) + ",";
      j += "\"max_lot\":" + DoubleToString(maxLot, 2) + ",";
      j += "\"lot_step\":" + DoubleToString(lotStep, 2) + ",";
      j += "\"trade_mode\":" + IntegerToString((int)tradeMode);
      j += "}";
      count++;
   }
   j += "]}\n";
   FileWriteString(wh, j);
   FileClose(wh);
}
