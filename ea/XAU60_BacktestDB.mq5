//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.60"
#property strict
#property description "SPECIALIZED FOR STRATEGY TESTER EXPORT"
#property description "v1.60: Auto-detects settings from Tester 'Settings' tab"

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // 1. System Restriction: Strategy Tester ONLY
   if(!MQLInfoInteger(MQL_TESTER))
   {
      Alert("CRITICAL ERROR: This EA can ONLY run in Strategy Tester.");
      Print("CRITICAL ERROR: This EA can ONLY run in Strategy Tester.");
      return(INIT_FAILED);
   }

   Print("XAU60_BacktestDB v1.60: Starting specialized export...");
   
   // 2. Auto-Detect Symbol & Timeframe (Uses current chart/test settings)
   string symbol = _Symbol;
   ENUM_TIMEFRAMES timeframe = _Period;
   string tf_str = StringSubstr(EnumToString(timeframe), 7);
   
   // 3. History Diagnostics
   int total_bars = Bars(symbol, timeframe);
   if(total_bars <= 0)
   {
      // Force loading of history
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

   // --- DEBUG LOG FILE ---
   string debug_log_file = "debug_XAU60.txt";
   int dh = FileOpen(debug_log_file, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(dh != INVALID_HANDLE)
   {
      FileWrite(dh, "--- XAU60_BacktestDB v1.60 Strategy Tester Report ---");
      FileWrite(dh, "Symbol: ", symbol);
      FileWrite(dh, "Timeframe: ", tf_str);
      FileWrite(dh, "Total Bars Found in Tester: ", total_bars);
      FileWrite(dh, "Start Date (Data): ", TimeToString(first_bar));
      FileWrite(dh, "End Date (Data):   ", TimeToString(last_bar));
      FileWrite(dh, "Max Bars Setting: ", (string)TerminalInfoInteger(TERMINAL_MAXBARS));
      FileWrite(dh, "--------------------------------------------------");
      FileClose(dh);
   }

   // 4. Resolve Range (Always export EVERYTHING available in the Tester)
   if(total_bars <= 0)
   {
      Print("CRITICAL ERROR: No data found in Strategy Tester. Check your 'Settings' tab.");
      return(INIT_FAILED);
   }

   // 5. Fetch and Export
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   Print("Attempting to export ", total_bars, " bars from ", TimeToString(first_bar), " to ", TimeToString(last_bar));

   ResetLastError();
   // Always copy by index for maximum stability
   int copied = CopyRates(symbol, timeframe, 0, total_bars, rates);
   if(copied <= 0)
   {
      Print("CRITICAL ERROR: CopyRates failed (Error Code: ", GetLastError(), ")");
      return(INIT_FAILED);
   }

   // 6. Build CSV Filename
   string start_str = TimeToString(rates[0].time, TIME_DATE); StringReplace(start_str, ".", "-");
   string end_str = TimeToString(rates[copied-1].time, TIME_DATE); StringReplace(end_str, ".", "-");
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", symbol, tf_str, start_str, end_str);
   
   int h = FileOpen(csv_filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
   {
      Print("CRITICAL ERROR: FileOpen failed: ", csv_filename);
      return(INIT_FAILED);
   }
   
   FileWrite(h, "time", "open", "high", "low", "close", "tick_volume");
   for(int i = 0; i < copied; i++)
   {
      FileWrite(h, (long)rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, (long)rates[i].tick_volume);
   }
   FileClose(h);

   Print("SUCCESS: ", copied, " bars exported to CSV.");
   Print("CSV Filename: ", csv_filename);

   // 7. Cleanup and Stop Tester
   ExpertRemove();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }
void OnTick() { }
