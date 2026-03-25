//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.40"
#property strict
#property description "Exports historical bar data to CSV for Parquet conversion"
#property description "Diagnostics included for solving 4401 (No history) errors"

//--- input parameters
input string          InpSymbol = "XAUUSD";      // Symbol
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M5;  // Timeframe
input datetime        InpStart = 0;              // Start Date (0 = Auto/Earliest)
input datetime        InpEnd = 0;                // End Date (0 = Auto/Current)

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("XAU60_BacktestDB v1.40: Starting diagnostics...");
   
   // --- Diagnostic: Terminal & Environment Info ---
   bool is_tester = (bool)MQLInfoInteger(MQL_TESTER);
   long max_bars = TerminalInfoInteger(TERMINAL_MAXBARS);
   string data_path = TerminalInfoString(TERMINAL_DATA_PATH);
   
   Print(" - Environment: ", is_tester ? "Strategy Tester" : "Live Chart");
   Print(" - Max bars in chart setting: ", (max_bars >= 10000000) ? "Unlimited" : (string)max_bars);
   Print(" - Data Path: ", data_path);

   // --- Diagnostic: Symbol Info ---
   if(!SymbolSelect(InpSymbol, true))
   {
      Print("CRITICAL ERROR: Symbol '", InpSymbol, "' not found in Market Watch.");
      return(INIT_FAILED);
   }
   
   // --- Diagnostic: History Availability ---
   ResetLastError();
   int total_bars = Bars(InpSymbol, InpTimeframe);
   if(total_bars <= 0)
   {
      Print("Warning: Bars() returned 0. Attempting history sync...");
      datetime dummy[];
      CopyTime(InpSymbol, InpTimeframe, 0, 1, dummy);
      total_bars = Bars(InpSymbol, InpTimeframe);
   }
   
   datetime first_bar = 0, last_bar = 0;
   datetime times[];
   if(total_bars > 0)
   {
      if(CopyTime(InpSymbol, InpTimeframe, total_bars - 1, 1, times) > 0) first_bar = times[0];
      if(CopyTime(InpSymbol, InpTimeframe, 0, 1, times) > 0) last_bar = times[0];
   }

   Print(" - Total Bars available: ", total_bars);
   Print(" - Earliest Bar: ", (first_bar > 0) ? TimeToString(first_bar) : "N/A");
   Print(" - Latest Bar:   ", (last_bar > 0)  ? TimeToString(last_bar)  : "N/A");

   // --- Resolve Range ---
   datetime start = InpStart;
   datetime end = InpEnd;
   if(start == 0) start = first_bar;
   if(end == 0) end = last_bar;

   // --- Data Extraction ---
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   Print(" - Requested Range: ", TimeToString(start), " to ", TimeToString(end));
   
   ResetLastError();
   int copied = CopyRates(InpSymbol, InpTimeframe, start, end, rates);
   
   if(copied <= 0)
   {
      int err = GetLastError();
      Print("CRITICAL ERROR: CopyRates failed (Error ", err, ").");
      
      if(err == 4401)
      {
         Print(" >>> DIAGNOSIS: History not found for the requested range.");
         if(is_tester) {
            Print(" >>> FIX: Change Strategy Tester 'From' date to match your requested range.");
         } else {
            Print(" >>> FIX: Open the chart for ", InpSymbol, ", and press HOME key to scroll back and download history.");
         }
      }
      else if(err == 4001) Print(" >>> DIAGNOSIS: Internal error. Try restarting MT5.");
      else if(err == 4302) Print(" >>> DIAGNOSIS: Symbol is not selected in Market Watch.");
      
      return(INIT_FAILED);
   }

   // --- Write CSV ---
   string tf_str = StringSubstr(EnumToString(InpTimeframe), 7);
   string start_str = TimeToString(rates[0].time, TIME_DATE); StringReplace(start_str, ".", "-");
   string end_str = TimeToString(rates[copied-1].time, TIME_DATE); StringReplace(end_str, ".", "-");
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", InpSymbol, tf_str, start_str, end_str);
   
   int handle = FileOpen(csv_filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("CRITICAL ERROR: Could not create file ", csv_filename);
      return(INIT_FAILED);
   }
   
   FileWrite(handle, "time", "open", "high", "low", "close", "tick_volume");
   for(int i = 0; i < copied; i++)
   {
      FileWrite(handle, (long)rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, (long)rates[i].tick_volume);
   }
   FileClose(handle);

   Print("SUCCESS: ", copied, " bars exported to ", csv_filename);
   Print("Full Path: ", data_path, "\\MQL5\\Files\\", csv_filename);
   
   ExpertRemove();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }
void OnTick() { }
