//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.70"
#property strict
#property description "SPECIALIZED FOR STRATEGY TESTER EXPORT"
#property description "v1.70: Saves to COMMON FILES FOLDER (Easy to find)"

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!MQLInfoInteger(MQL_TESTER))
   {
      Alert("CRITICAL: Use this EA in Strategy Tester ONLY!");
      return(INIT_FAILED);
   }

   Print("XAU60_BacktestDB v1.70: Saving to Common folder...");
   
   string symbol = _Symbol;
   ENUM_TIMEFRAMES timeframe = _Period;
   string tf_str = StringSubstr(EnumToString(timeframe), 7);
   
   int total_bars = Bars(symbol, timeframe);
   if(total_bars <= 0)
   {
      datetime dummy[];
      CopyTime(symbol, timeframe, 0, 1, dummy);
      total_bars = Bars(symbol, timeframe);
   }
   
   datetime first_bar = 0, last_bar = 0;
   datetime times[];
   if(total_bars > 0)
   {
      if(CopyTime(symbol, timeframe, total_bars - 1, 1, times) > 0) first_bar = times[0];
      if(CopyTime(symbol, timeframe, 0, 1, times) > 0) last_bar = times[0];
   }

   // --- DEBUG LOG FILE (Saves to Common folder) ---
   string debug_log_file = "debug_XAU60.txt";
   int dh = FileOpen(debug_log_file, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(dh != INVALID_HANDLE)
   {
      FileWrite(dh, "--- XAU60_BacktestDB v1.70 COMMON REPORT ---");
      FileWrite(dh, "Symbol: ", symbol);
      FileWrite(dh, "Timeframe: ", tf_str);
      FileWrite(dh, "Bars Found: ", total_bars);
      FileWrite(dh, "Range: ", TimeToString(first_bar), " - ", TimeToString(last_bar));
      FileClose(dh);
   }

   if(total_bars <= 0) return(INIT_FAILED);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(symbol, timeframe, 0, total_bars, rates);
   if(copied <= 0) return(INIT_FAILED);

   string start_str = TimeToString(rates[0].time, TIME_DATE); StringReplace(start_str, ".", "-");
   string end_str = TimeToString(rates[copied-1].time, TIME_DATE); StringReplace(end_str, ".", "-");
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", symbol, tf_str, start_str, end_str);
   
   // --- CSV FILE (Saves to Common folder) ---
   int h = FileOpen(csv_filename, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
   {
      Print("CRITICAL ERROR: FileOpen (Common) failed: ", csv_filename);
      return(INIT_FAILED);
   }
   
   FileWrite(h, "time", "open", "high", "low", "close", "tick_volume");
   for(int i = 0; i < copied; i++)
   {
      FileWrite(h, (long)rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, (long)rates[i].tick_volume);
   }
   FileClose(h);

   Print("SUCCESS: File saved to COMMON folder.");
   Print("Filename: ", csv_filename);
   Print("Path Tips: Look for 'Common/Files' inside MetaQuotes/Terminal directory.");

   ExpertRemove();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }
void OnTick() { }
