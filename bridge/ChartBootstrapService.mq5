//+------------------------------------------------------------------+
//|                                  ChartBootstrapService.mq5      |
//|   Trading OS — auto-open charts and apply bridge tpl  |
//|   Reads trading-os/chart_manifest.csv from FILE_COMMON (ipc)    |
//+------------------------------------------------------------------+
#property copyright "Trading OS"
#property link      "https://trading-os.local"
#property version   "1.01"
#property strict

input string InpIpcPrefix      = "trading-os/";
input string InpManifestFile   = "chart_manifest.csv";
input string InpTemplateName   = "trading_os_bridge";   // Profiles/Templates/<name>.tpl
input string InpTemplateFallback = "trading_os"; // alternate manual save name
input string InpTemplateFallback2 = ""; // legacy fallback removed
input string InpBridgeEAName   = "FileBridgeEA_MultiSymbol";
input int    InpRefreshSec     = 300;
input int    InpHeartbeatMaxAgeSec = 120;
input bool   InpVerbose        = true;

//+------------------------------------------------------------------+
int OnInit()
{
   EventSetTimer(MathMax(30, InpRefreshSec));
   EnsureBridgeTemplate("init");
   RunBootstrapPass("init");
   return(INIT_SUCCEEDED);
}
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
}
//+------------------------------------------------------------------+
void OnTimer()
{
   RunBootstrapPass("timer");
}
//+------------------------------------------------------------------+
void RunBootstrapPass(string trigger)
{
   string path = InpIpcPrefix + InpManifestFile;
   if(!FileIsExist(path, FILE_COMMON))
   {
      LogLine("manifest_missing", path);
      return;
   }

   int h = FileOpen(path, FILE_READ|FILE_TXT|FILE_COMMON);
   if(h == INVALID_HANDLE)
   {
      LogLine("manifest_open_failed", path);
      return;
   }

   int opened = 0;
   int skipped = 0;
   int failed = 0;
   int attached = 0;

   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      if(StringLen(line) < 3) continue;
      if(StringFind(line, "symbol,") == 0) continue; // header

      string parts[];
      int n = StringSplit(line, StringGetCharacter(",", 0), parts);
      if(n < 6) continue;

      string broker = parts[1];
      string chartLabel = parts[2];
      int mt5Period = (int)StringToInteger(parts[4]);
      if(StringLen(broker) == 0) continue;

      if(ChartHeartbeatFresh(chartLabel))
      {
         skipped++;
         continue;
      }

      ENUM_TIMEFRAMES tf = PeriodFromMinutes(mt5Period);
      if(!SymbolSelect(broker, true))
      {
         failed++;
         LogLine("symbol_select_failed", broker);
         continue;
      }

      long chartId = FindChart(broker, tf);
      if(chartId < 0)
         chartId = ChartOpen(broker, tf);

      if(chartId < 0)
      {
         failed++;
         LogLine("chart_open_failed", broker + " tf=" + IntegerToString(mt5Period));
         continue;
      }

      opened++;
      ChartSetInteger(chartId, CHART_SHOW_GRID, false);
      ChartSetInteger(chartId, CHART_AUTOSCROLL, true);
      ChartSetInteger(chartId, CHART_SHIFT, true);

      if(ChartHasBridgeExpert(chartId))
      {
         attached++;
         LogLine("bridge_already_attached", broker + " chart_id=" + IntegerToString(chartId));
         continue;
      }

      if(!AttachBridgeToChart(chartId, broker))
      {
         failed++;
         LogLine("bridge_attach_failed", broker + " chart_id=" + IntegerToString(chartId));
         continue;
      }

      attached++;
      LogLine("bridge_attached", broker + " chart_id=" + IntegerToString(chartId));
   }

   FileClose(h);
   LogLine("pass_complete", trigger + " opened=" + IntegerToString(opened)
           + " attached=" + IntegerToString(attached)
           + " skipped=" + IntegerToString(skipped)
           + " failed=" + IntegerToString(failed));
}
//+------------------------------------------------------------------+
string ResolveTemplateName()
{
   if(TemplateExists(InpTemplateName))
      return InpTemplateName;
   if(StringLen(InpTemplateFallback) > 0 && TemplateExists(InpTemplateFallback))
      return InpTemplateFallback;
   if(StringLen(InpTemplateFallback2) > 0 && TemplateExists(InpTemplateFallback2))
      return InpTemplateFallback2;
   return InpTemplateName;
}
//+------------------------------------------------------------------+
bool EnsureBridgeTemplate(string trigger)
{
   string tpl = ResolveTemplateName();
   if(TemplateExists(tpl))
   {
      LogLine("template_ready", tpl + " source=profiles");
      return true;
   }

   long refChart = FindChartWithBridgeExpert();
   if(refChart < 0)
   {
      LogLine("template_reference_missing",
              "Attach " + InpBridgeEAName + " to one chart (e.g. EURUSD). "
              + "Or save template as " + InpTemplateName + " or " + InpTemplateFallback);
      return false;
   }

   ResetLastError();
   if(!ChartSaveTemplate(refChart, InpTemplateName))
   {
      LogLine("template_save_failed", InpTemplateName + " err=" + IntegerToString(GetLastError())
              + " ref=" + ChartSymbol(refChart));
      return false;
   }

   LogLine("template_saved", InpTemplateName + " from=" + ChartSymbol(refChart) + " trigger=" + trigger);
   return true;
}
//+------------------------------------------------------------------+
bool AttachBridgeToChart(const long chartId, const string broker)
{
   if(!TemplateExists(ResolveTemplateName()))
      EnsureBridgeTemplate("attach");

   string tpl = ResolveTemplateName();
   if(StringLen(tpl) > 0)
   {
      ResetLastError();
      if(ChartApplyTemplate(chartId, tpl))
      {
         if(ChartHasBridgeExpert(chartId))
            return true;
         LogLine("template_applied_no_expert", broker + " tpl=" + tpl
                 + " expert=" + ChartGetString(chartId, CHART_EXPERT_NAME));
      }
      else
      {
         LogLine("template_apply_failed", broker + " tpl=" + tpl
                 + " err=" + IntegerToString(GetLastError()));
      }
   }

   return ChartHasBridgeExpert(chartId);
}
//+------------------------------------------------------------------+
bool TemplateExists(const string templateName)
{
   string rel = "Profiles\\Templates\\" + templateName + ".tpl";
   int h = FileOpen(rel, FILE_READ|FILE_BIN);
   if(h == INVALID_HANDLE)
      return false;
   FileClose(h);
   return true;
}
//+------------------------------------------------------------------+
long FindChartWithBridgeExpert()
{
   long id = ChartFirst();
   while(id >= 0)
   {
      if(ChartHasBridgeExpert(id))
         return id;
      id = ChartNext(id);
   }
   return -1;
}
//+------------------------------------------------------------------+
bool ChartHasBridgeExpert(const long chartId)
{
   string expert = ChartGetString(chartId, CHART_EXPERT_NAME);
   if(StringLen(expert) < 1)
      return false;
   return (StringFind(expert, InpBridgeEAName) >= 0);
}
//+------------------------------------------------------------------+
bool ChartHeartbeatFresh(string chartLabel)
{
   string hbFile = InpIpcPrefix + chartLabel + "/heartbeat.txt";
   if(!FileIsExist(hbFile, FILE_COMMON)) return false;

   int h = FileOpen(hbFile, FILE_READ|FILE_TXT|FILE_COMMON);
   if(h == INVALID_HANDLE) return false;
   string line = FileReadString(h);
   FileClose(h);
   if(StringLen(line) < 1) return false;

   string stampStr = line;
   if(StringFind(line, "|") >= 0)
      stampStr = StringSubstr(line, 0, StringFind(line, "|"));

   long stamp = (long)StringToInteger(stampStr);
   if(stamp <= 0) return false;
   long age = (long)TimeLocal() - stamp;
   return (age >= 0 && age <= InpHeartbeatMaxAgeSec);
}
//+------------------------------------------------------------------+
long FindChart(string symbol, ENUM_TIMEFRAMES tf)
{
   long id = ChartFirst();
   while(id >= 0)
   {
      if(ChartSymbol(id) == symbol && ChartPeriod(id) == tf)
         return id;
      id = ChartNext(id);
   }
   return -1;
}
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES PeriodFromMinutes(int minutes)
{
   switch(minutes)
   {
      case 1:   return PERIOD_M1;
      case 5:   return PERIOD_M5;
      case 15:  return PERIOD_M15;
      case 30:  return PERIOD_M30;
      case 60:  return PERIOD_H1;
      case 240: return PERIOD_H4;
      case 1440:return PERIOD_D1;
      default:  return PERIOD_M15;
   }
}
//+------------------------------------------------------------------+
void LogLine(string event, string detail)
{
   string path = InpIpcPrefix + "chart_bootstrap.log";
   int h = FileOpen(path, FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h == INVALID_HANDLE)
      h = FileOpen(path, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   string line = IntegerToString((long)TimeLocal()) + "|" + event + "|" + detail + "\n";
   FileWriteString(h, line);
   FileClose(h);
   if(InpVerbose)
      Print("[ChartBootstrapService] ", event, ": ", detail);
}
//+------------------------------------------------------------------+
