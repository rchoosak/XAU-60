//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.10"
#property strict
#property description "Exports historical bar data to CSV for Parquet conversion"
#property description "Updated: Auto-detect range if start/end is 0"

//--- input parameters
input string          InpSymbol = "XAUUSD";      // Symbol
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M5;  // Timeframe
input datetime        InpStart = 0;              // Start Date (0 = Auto)
input datetime        InpEnd = 0;                // End Date (0 = Auto)

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("XAU60_BacktestDB: Starting data export...");
   
   datetime start = InpStart;
   datetime end = InpEnd;
   
   // 1. Resolve Auto Dates
   if(end == 0) end = TimeCurrent();
   if(start == 0)
   {
      // Find the earliest available bar
      datetime times[];
      int total_bars = Bars(InpSymbol, InpTimeframe);
      if(total_bars > 0 && CopyTime(InpSymbol, InpTimeframe, total_bars - 1, 1, times) > 0)
      {
         start = times[0];
      }
      else 
      {
         start = D'1970.01.01'; // Fallback
      }
   }

   // 2. Data Validation
   if(start >= end)
   {
      Print("Error: Start Date must be before End Date.");
      return(INIT_FAILED);
   }

   // 3. Fetch Historical Data
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   int copied = CopyRates(InpSymbol, InpTimeframe, start, end, rates);
   if(copied <= 0)
   {
      Print("Error: No data found for ", InpSymbol, " (", EnumToString(InpTimeframe), ") in the specified range.");
      return(INIT_FAILED);
   }
   
   // Actual range from data
   datetime actual_start = rates[0].time;
   datetime actual_end = rates[copied-1].time;
   
   Print("XAU60_BacktestDB: Fetched ", copied, " bars.");
   Print("Data Range: ", TimeToString(actual_start), " to ", TimeToString(actual_end));

   // 4. Define Filename
   string tf_name = EnumToString(InpTimeframe);
   string tf_str = StringSubstr(tf_name, 7); // Remove PERIOD_
   
   string start_str = TimeToString(actual_start, TIME_DATE);
   StringReplace(start_str, ".", "-");
   string end_str = TimeToString(actual_end, TIME_DATE);
   StringReplace(end_str, ".", "-");
   
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", InpSymbol, tf_str, start_str, end_str);
   
   // 5. Write to CSV
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
   
   // 6. Parquet Note
   string parquet_name = StringFormat("%s_%s_%s-%s.parquet", InpSymbol, tf_str, start_str, end_str);
   Print("To convert to Parquet, run:");
   Print("python3 scripts/csv_to_parquet.py --input \"MQL5/Files/", csv_filename, "\" --output \"data/backtest-db/", parquet_name, "\"");

   // Finished
   ExpertRemove();
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("XAU60_BacktestDB: Deinitialized.");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Handled in OnInit
}
//+------------------------------------------------------------------+
