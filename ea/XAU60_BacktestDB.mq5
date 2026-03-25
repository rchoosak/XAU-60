//+------------------------------------------------------------------+
//|                                           XAU60_BacktestDB.mq5   |
//|                                  Copyright 2024, Choosak.R       |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Choosak.R"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict
#property description "Exports historical bar data to CSV for Parquet conversion"

//--- input parameters
input string          InpSymbol = "XAUUSD";      // Symbol
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M5;  // Timeframe
input datetime        InpStart = D'2020.01.01';  // Start Date
input datetime        InpEnd = D'2026.03.24';    // End Date

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("XAU60_BacktestDB: Starting data export...");
   
   // 1. Data Validation
   if(InpStart >= InpEnd)
   {
      Print("Error: Start Date must be before End Date.");
      return(INIT_FAILED);
   }

   // 2. Fetch Historical Data
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   
   int copied = CopyRates(InpSymbol, InpTimeframe, InpStart, InpEnd, rates);
   if(copied <= 0)
   {
      Print("Error: No data found for ", InpSymbol, " (", EnumToString(InpTimeframe), ") in the specified range.");
      return(INIT_FAILED);
   }
   
   Print("XAU60_BacktestDB: Fetched ", copied, " bars.");

   // 3. Define Filename
   string tf_name = EnumToString(InpTimeframe);
   string tf_str = StringSubstr(tf_name, 7); // Remove PERIOD_
   
   string start_str = TimeToString(InpStart, TIME_DATE);
   StringReplace(start_str, ".", "-");
   string end_str = TimeToString(InpEnd, TIME_DATE);
   StringReplace(end_str, ".", "-");
   
   string csv_filename = StringFormat("%s_%s_%s_%s.csv", InpSymbol, tf_str, start_str, end_str);
   
   // 4. Write to CSV
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
   
   // 5. Parquet Note
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
