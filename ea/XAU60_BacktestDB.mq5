//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.50"
#property strict
#property description "Exports historical bar data to CSV for Parquet conversion"
#property description "v1.50: Use current chart timeframe if input is PERIOD_CURRENT"

//--- input parameters
input string          InpSymbol = "XAUUSD";         // Symbol
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_CURRENT; // Timeframe (PERIOD_CURRENT = Use Chart)
input datetime        InpStart = 0;                 // Start Date (0 = Auto/Earliest)
input datetime        InpEnd = 0;                   // End Date (0 = Auto/Current)

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("XAU60_BacktestDB v1.50: Initialization...");
   
   ENUM_TIMEFRAMES tf = (InpTimeframe == PERIOD_CURRENT) ? _Period : InpTimeframe;
   string tf_str = StringSubstr(EnumToString(tf), 7);
   
   // --- Diagnostic: Environment ---
   bool is_tester = (bool)MQLInfoInteger(MQL_TESTER);
   long max_bars = TerminalInfoInteger(TERMINAL_MAXBARS);
   string data_path = TerminalInfoString(TERMINAL_DATA_PATH);

   if(!SymbolSelect(InpSymbol, true))
   {
      Print("CRITICAL ERROR: Symbol '", InpSymbol, "' not found.");
      return(INIT_FAILED);
   }
   
   // --- Diagnostic: History Availability ---
   int total_bars = Bars(InpSymbol, tf);
   if(total_bars <= 0)
   {
      datetime dummy[];
      CopyTime(InpSymbol, tf, 0, 1, dummy);
      total_bars = Bars(InpSymbol, tf);
   }
   
   datetime first_bar = 0, last_bar = 0;
   datetime times[];
   if(total_bars > 0)
   {
      if(CopyTime(InpSymbol, tf, total_bars - 1, 1, times) > 0) first_bar = times[0];
      if(CopyTime(InpSymbol, tf, 0, 1, times) > 0) last_bar = times[0];
   }

   // --- Log Diagnostic to File (so it's easier to copy) ---
   string diag_file = StringFormat("debug_%s_%s.txt", InpSymbol, tf_str);
   int dh = FileOpen(diag_file, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(dh != INVALID_HANDLE)
   {
      FileWrite(dh, "XAU60_BacktestDB v1.50 Diagnostics");
      FileWrite(dh, "=================================");
      FileWrite(dh, "Environment: ", is_tester ? "Strategy Tester" : "Live Chart");
      FileWrite(dh, "Symbol: ", InpSymbol);
      FileWrite(dh, "Timeframe: ", tf_str);
      FileWrite(dh, "Max Bars Setting: ", (max_bars >= 10000000) ? "Unlimited" : (string)max_bars);
      FileWrite(dh, "Total Bars in Machine: ", total_bars);
      FileWrite(dh, "Earliest Bar Found: ", TimeToString(first_bar));
      FileWrite(dh, "Latest Bar Found:   ", TimeToString(last_bar));
      FileWrite(dh, "---------------------------------");
      FileClose(dh);
   }

   Print(" - Detected history from ", TimeToString(first_bar), " to ", TimeToString(last_bar));

   // --- Resolve Range ---
   datetime start = InpStart;
   datetime end = InpEnd;
   if(start == 0) start = first_bar;
   if(end == 0) end = last_bar;

   // --- Fetch Data ---
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   ResetLastError();
   int copied = CopyRates(InpSymbol, tf, start, end, rates);
   
   if(copied <= 0)
   {
      int err = GetLastError();
      Print("CRITICAL ERROR: CopyRates failed (Error ", err, ")");
      if(err == 4401) {
         Print(" >>> DIAGNOSIS: No history from ", TimeToString(start), " onwards!");
         Print(" >>> Latest bar available is: ", TimeToString(last_bar));
      }
      return(INIT_FAILED);
   }

   // --- Write CSV ---
   string start_str = TimeToString(rates[0].time, TIME_DATE); StringReplace(start_str, ".", "-");
   string end_str = TimeToString(rates[copied-1].time, TIME_DATE); StringReplace(end_str, ".", "-");
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", InpSymbol, tf_str, start_str, end_str);
   
   int h = FileOpen(csv_filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
   {
      Print("CRITICAL ERROR: Could not create ", csv_filename);
      return(INIT_FAILED);
   }
   
   FileWrite(h, "time", "open", "high", "low", "close", "tick_volume");
   for(int i = 0; i < copied; i++)
   {
      FileWrite(h, (long)rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, (long)rates[i].tick_volume);
   }
   FileClose(h);

   Print("SUCCESS: ", copied, " bars exported to ", csv_filename);
   ExpertRemove();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }
void OnTick() { }
