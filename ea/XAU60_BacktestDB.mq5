//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.30"
#property strict
#property description "Exports historical bar data to CSV for Parquet conversion"

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
   Print("XAU60_BacktestDB v1.30: Analyzing history...");
   
   // 1. Symbol Validation
   if(!SymbolSelect(InpSymbol, true))
   {
      Print("Error: Symbol ", InpSymbol, " not found.");
      return(INIT_FAILED);
   }

   // 2. Check History Availability and Range
   int total_bars = Bars(InpSymbol, InpTimeframe);
   if(total_bars <= 0)
   {
      Print("No history found. Touching history...");
      datetime dummy[];
      CopyTime(InpSymbol, InpTimeframe, 0, 1, dummy);
      total_bars = Bars(InpSymbol, InpTimeframe);
   }
   
   if(total_bars <= 0)
   {
      Print("Error: Still no history available for ", InpSymbol);
      return(INIT_FAILED);
   }

   datetime first_bar_time = 0;
   datetime last_bar_time = 0;
   datetime times[];
   
   if(CopyTime(InpSymbol, InpTimeframe, total_bars - 1, 1, times) > 0) first_bar_time = times[0];
   if(CopyTime(InpSymbol, InpTimeframe, 0, 1, times) > 0) last_bar_time = times[0];

   Print("History available in Terminal/Tester:");
   Print(" - Total Bars: ", total_bars);
   Print(" - Oldest Bar: ", TimeToString(first_bar_time));
   Print(" - Newest Bar: ", TimeToString(last_bar_time));

   // 3. Resolve Export Range
   datetime start = InpStart;
   datetime end = InpEnd;
   
   if(start == 0) start = first_bar_time;
   if(end == 0) end = last_bar_time;
   
   // Safety: Ensure start is not before first_bar_time
   if(start < first_bar_time) 
   {
      Print("Warning: Requested start ", TimeToString(start), " is earlier than history. Using ", TimeToString(first_bar_time));
      start = first_bar_time;
   }
   if(end > last_bar_time) end = last_bar_time;

   // 4. Fetch Data using Count to avoid 4401 range errors
   int start_pos = iBarShift(InpSymbol, InpTimeframe, start, true);
   int end_pos = iBarShift(InpSymbol, InpTimeframe, end, true);
   
   if(start_pos < 0) start_pos = total_bars - 1;
   if(end_pos < 0) end_pos = 0;
   
   int bars_to_copy = start_pos - end_pos + 1;
   if(bars_to_copy <= 0)
   {
      Print("Error: Calculated bars to copy is ", bars_to_copy, ". Check your dates.");
      return(INIT_FAILED);
   }

   Print("Attempting to copy ", bars_to_copy, " bars starting from index ", start_pos);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   ResetLastError();
   // Use start_pos and count method (most reliable)
   int copied = CopyRates(InpSymbol, InpTimeframe, end_pos, bars_to_copy, rates);
   if(copied <= 0)
   {
      Print("Error: CopyRates failed. Method: Index. Code: ", GetLastError());
      // Fallback to time-range method
      copied = CopyRates(InpSymbol, InpTimeframe, start, end, rates);
      if(copied <= 0)
      {
         Print("Error: CopyRates failed. Method: TimeRange. Code: ", GetLastError());
         return(INIT_FAILED);
      }
   }
   
   datetime actual_start = rates[0].time;
   datetime actual_end = rates[copied-1].time;
   
   Print("XAU60_BacktestDB: Exporting ", copied, " bars (", TimeToString(actual_start), " to ", TimeToString(actual_end), ")");

   // 5. Build Filename
   string tf_str = StringSubstr(EnumToString(InpTimeframe), 7);
   string start_str = TimeToString(actual_start, TIME_DATE); StringReplace(start_str, ".", "-");
   string end_str = TimeToString(actual_end, TIME_DATE); StringReplace(end_str, ".", "-");
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", InpSymbol, tf_str, start_str, end_str);
   
   // 6. Write CSV
   int handle = FileOpen(csv_filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("Error: FileOpen failed: ", csv_filename);
      return(INIT_FAILED);
   }
   
   FileWrite(handle, "time", "open", "high", "low", "close", "tick_volume");
   for(int i = 0; i < copied; i++)
   {
      FileWrite(handle, (long)rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, rates[i].tick_volume);
   }
   FileClose(handle);
   
   Print("Success! File: ", csv_filename, " in MQL5/Files/");
   ExpertRemove();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }
void OnTick() { }
