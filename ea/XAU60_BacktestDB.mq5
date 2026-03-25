//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.20"
#property strict
#property description "Exports historical bar data to CSV for Parquet conversion"
#property description "Optimized for Strategy Tester range detection"

//--- input parameters
input string          InpSymbol = "XAUUSD";      // Symbol
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M1;  // Timeframe
input datetime        InpStart = D'2020.01.01';  // Start Date (0 = Auto)
input datetime        InpEnd = D'2026.03.24';    // End Date (0 = Auto)

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("XAU60_BacktestDB: Starting initialization...");
   
   // 1. Symbol Validation
   if(!SymbolSelect(InpSymbol, true))
   {
      Print("Error: Symbol ", InpSymbol, " not found or not selectable.");
      return(INIT_FAILED);
   }

   // 2. Check History Availability
   int total_bars = Bars(InpSymbol, InpTimeframe);
   Print("Total bars available for ", InpSymbol, " (", EnumToString(InpTimeframe), "): ", total_bars);
   
   if(total_bars <= 0)
   {
      Print("Waiting for history synchronization...");
      // In Tester, we might need to "touch" the data to trigger loading
      datetime dummy[];
      CopyTime(InpSymbol, InpTimeframe, 0, 1, dummy);
      total_bars = Bars(InpSymbol, InpTimeframe);
      Print("Bars after sync attempt: ", total_bars);
   }

   datetime start = InpStart;
   datetime end = InpEnd;
   
   // 3. Resolve Auto Dates
   if(end == 0) end = TimeCurrent();
   if(start == 0)
   {
      datetime times[];
      if(total_bars > 0 && CopyTime(InpSymbol, InpTimeframe, total_bars - 1, 1, times) > 0)
      {
         start = times[0];
      }
      else 
      {
         start = D'1970.01.01'; // Fallback
      }
   }

   Print("Requested Range: ", TimeToString(start), " to ", TimeToString(end));

   // 4. Fetch Historical Data
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   ResetLastError();
   int copied = CopyRates(InpSymbol, InpTimeframe, start, end, rates);
   if(copied <= 0)
   {
      int error = GetLastError();
      Print("Error: No data found for ", InpSymbol, " in range. CopyRates returned: ", copied, " Error Code: ", error);
      if(error == 4401) Print("Hint: History not found. Try scrolling the chart back manually or use Strategy Tester.");
      return(INIT_FAILED);
   }
   
   // Actual range from data
   datetime actual_start = rates[0].time;
   datetime actual_end = rates[copied-1].time;
   
   Print("XAU60_BacktestDB: Successfully fetched ", copied, " bars.");
   Print("Final Data Range: ", TimeToString(actual_start), " to ", TimeToString(actual_end));

   // 5. Define Filename
   string tf_name = EnumToString(InpTimeframe);
   string tf_str = StringSubstr(tf_name, 7); // Remove PERIOD_
   
   string start_str = TimeToString(actual_start, TIME_DATE);
   StringReplace(start_str, ".", "-");
   string end_str = TimeToString(actual_end, TIME_DATE);
   StringReplace(end_str, ".", "-");
   
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", InpSymbol, tf_str, start_str, end_str);
   
   // 6. Write to CSV
   int handle = FileOpen(csv_filename, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("Error: Could not open file for writing: ", csv_filename);
      return(INIT_FAILED);
   }
   
   // Headers
   FileWrite(handle, "time", "open", "high", "low", "close", "tick_volume");
   
   // Loop through data
   for(int i = 0; i < copied; i++)
   {
      FileWrite(handle, 
         (long)rates[i].time,
         rates[i].open,
         rates[i].high,
         rates[i].low,
         rates[i].close,
         rates[i].tick_volume
      );
   }
   
   FileClose(handle);
   
   string data_path = TerminalInfoString(TERMINAL_DATA_PATH);
   Print("XAU60_BacktestDB: Exported to CSV successfully.");
   Print("CSV Filename: ", csv_filename);
   Print("Location: ", data_path, "\\MQL5\\Files\\");
   
   // 7. Parquet Note
   string parquet_name = StringFormat("%s_%s_%s-%s.parquet", InpSymbol, tf_str, start_str, end_str);
   Print("To convert to Parquet, run:");
   Print("python3 scripts/csv_to_parquet.py --input \"MQL5/Files/", csv_filename, "\" --output \"data/backtest-db/", parquet_name, "\"");

   // Finished
   ExpertRemove();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { Print("XAU60_BacktestDB: Deinitialized."); }
void OnTick() { }
