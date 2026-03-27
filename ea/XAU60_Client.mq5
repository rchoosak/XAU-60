#property strict
#property description "HTTP polling bridge for the Python MT5 bot"

input string BridgeBaseUrl = "http://10.0.2.2:8876";
input string BridgeToken = "change-me";
input int PollIntervalMs = 1000;
input int PollTimeoutMs = 25000;
input int RequestTimeoutMs = 10000;
input int S1CacheLookbackSeconds = 21600; // 6 hours of tick-based S1 cache
input int S1CacheMaxBars = 30000; // cap memory usage for cached S1 bars

string g_base_url = "";
datetime g_last_processed = 0;
datetime g_last_idle_log = 0;
bool g_warned_s1_fallback = false;
bool g_warned_s1_cache_mode = false;
string g_s1_cache_symbol = "";
MqlRates g_s1_cache_rates[];
long g_s1_cache_last_tick_msc = 0;
bool g_s1_cache_ready = false;

int OnInit()
{
   g_base_url = (StringLen(BridgeBaseUrl) > 0 && StringSubstr(BridgeBaseUrl, StringLen(BridgeBaseUrl) - 1, 1) == "/") ?
      StringSubstr(BridgeBaseUrl, 0, StringLen(BridgeBaseUrl) - 1) :
      BridgeBaseUrl;

   EventSetMillisecondTimer(MathMax(PollIntervalMs, 250));
   Print("XAU60_Client initialized. Add this URL to MT5 WebRequest allowlist: ", g_base_url);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   PollBridge();
}

void PollBridge()
{
   string headers = "";
   string url = g_base_url + "/poll?timeout_ms=" + IntegerToString(PollTimeoutMs) + "&token=" + BridgeToken;

   char post_data[];
   char response[];
   string response_headers = "";
   ResetLastError();
   int status = WebRequest("GET", url, headers, RequestTimeoutMs + PollTimeoutMs, post_data, response, response_headers);
   if(status == -1)
   {
      int error = GetLastError();
      Print("Bridge poll failed. WebRequest error: ", error, " url=", url);
      return;
   }

   string body = CharArrayToString(response, 0, ArraySize(response), CP_UTF8);
   if(StringLen(body) == 0)
   {
      Print("Bridge poll returned empty body. HTTP status=", status);
      return;
   }

   string status_value = GetLineValue(body, "status");
   if(status_value == "empty")
   {
      datetime now = TimeCurrent();
      if(g_last_idle_log == 0 || now - g_last_idle_log >= 30)
      {
         Print("Bridge poll ok, no pending requests. HTTP status=", status);
         g_last_idle_log = now;
      }
      return;
   }
   if(status_value != "ok")
   {
      Print("Bridge poll returned unexpected payload. HTTP status=", status, " body=", body);
      return;
   }

   string request_id = GetLineValue(body, "request_id");
   string action = GetLineValue(body, "action");
   if(request_id == "" || action == "")
   {
      Print("Bridge poll missing request metadata. body=", body);
      return;
   }

   string result_json = "";
   string error_message = "";
   bool ok = ExecuteAction(body, action, result_json, error_message);
   Print("Processing bridge action: ", action, " request_id=", request_id, " ok=", ok);
   PostResponse(request_id, ok, result_json, error_message);
   g_last_processed = TimeCurrent();
}

bool ExecuteAction(const string request_body, const string action, string &result_json, string &error_message)
{
   if(action == "terminal_info")
      return BuildTerminalInfo(result_json, error_message);
   if(action == "account_info")
      return BuildAccountInfo(result_json, error_message);
   if(action == "symbol_select")
      return HandleSymbolSelect(request_body, result_json, error_message);
   if(action == "symbol_info")
      return BuildSymbolInfo(GetParam(request_body, "symbol"), result_json, error_message);
   if(action == "symbol_info_tick")
      return BuildSymbolTick(GetParam(request_body, "symbol"), result_json, error_message);
   if(action == "copy_rates_from_pos")
      return HandleCopyRatesFromPos(request_body, result_json, error_message);
   if(action == "copy_rates_from")
      return HandleCopyRatesFrom(request_body, result_json, error_message);
   if(action == "copy_rates_range")
      return HandleCopyRatesRange(request_body, result_json, error_message);
   if(action == "order_send")
      return HandleOrderSend(request_body, result_json, error_message);
   if(action == "positions_get")
      return HandlePositionsGet(request_body, result_json, error_message);
   if(action == "history_deals_get")
      return HandleHistoryDealsGet(request_body, result_json, error_message);

   error_message = "Unsupported action: " + action;
   return false;
}

bool HandleSymbolSelect(const string request_body, string &result_json, string &error_message)
{
   string symbol = GetParam(request_body, "symbol");
   bool enable = StringToInteger(GetParam(request_body, "enable")) != 0;
   if(symbol == "")
   {
      error_message = "Missing symbol";
      return false;
   }

   bool success = SymbolSelect(symbol, enable);
   result_json = success ? "true" : "false";
   if(!success)
      error_message = "SymbolSelect failed";
   return success;
}

bool HandleCopyRatesFromPos(const string request_body, string &result_json, string &error_message)
{
   string symbol = GetParam(request_body, "symbol");
   string timeframe_value = GetParam(request_body, "timeframe");
   int start_pos = (int)StringToInteger(GetParam(request_body, "start_pos"));
   int count = (int)StringToInteger(GetParam(request_body, "count"));
   bool use_s1_cache = IsS1Request(timeframe_value) && !SupportsPeriodS1();

   MqlRates rates[];
   int copied = -1;
   if(use_s1_cache)
      copied = CopyS1RatesFromPosCached(symbol, start_pos, count, rates);
   else
   {
      ENUM_TIMEFRAMES timeframe = ParseTimeframe(timeframe_value);
      copied = CopyRates(symbol, timeframe, start_pos, count, rates);
   }
   if(copied < 0)
   {
      error_message = use_s1_cache ? "S1 cache copy from pos failed" : "CopyRates from pos failed";
      return false;
   }

   result_json = RatesToJson(rates, copied);
   return true;
}

bool HandleCopyRatesFrom(const string request_body, string &result_json, string &error_message)
{
   string symbol = GetParam(request_body, "symbol");
   string timeframe_value = GetParam(request_body, "timeframe");
   datetime start_time = (datetime)StringToInteger(GetParam(request_body, "start_time"));
   int count = (int)StringToInteger(GetParam(request_body, "count"));
   bool use_s1_cache = IsS1Request(timeframe_value) && !SupportsPeriodS1();

   MqlRates rates[];
   int copied = -1;
   if(use_s1_cache)
      copied = CopyS1RatesFromCached(symbol, start_time, count, rates);
   else
   {
      ENUM_TIMEFRAMES timeframe = ParseTimeframe(timeframe_value);
      copied = CopyRates(symbol, timeframe, start_time, count, rates);
   }
   if(copied < 0)
   {
      error_message = use_s1_cache ? "S1 cache copy failed" : "CopyRates failed";
      return false;
   }

   result_json = RatesToJson(rates, copied);
   return true;
}

bool HandleCopyRatesRange(const string request_body, string &result_json, string &error_message)
{
   string symbol = GetParam(request_body, "symbol");
   string timeframe_value = GetParam(request_body, "timeframe");
   datetime start_time = (datetime)StringToInteger(GetParam(request_body, "start_time"));
   datetime end_time = (datetime)StringToInteger(GetParam(request_body, "end_time"));
   bool use_s1_cache = IsS1Request(timeframe_value) && !SupportsPeriodS1();

   MqlRates rates[];
   int copied = -1;
   if(use_s1_cache)
      copied = CopyS1RatesRangeCached(symbol, start_time, end_time, rates);
   else
   {
      ENUM_TIMEFRAMES timeframe = ParseTimeframe(timeframe_value);
      copied = CopyRates(symbol, timeframe, start_time, end_time, rates);
   }
   if(copied < 0)
   {
      error_message = use_s1_cache ? "S1 cache copy range failed" : "CopyRates range failed";
      return false;
   }

   result_json = RatesToJson(rates, copied);
   return true;
}

bool HandleOrderSend(const string request_body, string &result_json, string &error_message)
{
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = (ENUM_TRADE_REQUEST_ACTIONS)StringToInteger(GetParam(request_body, "action"));
   request.symbol = GetParam(request_body, "symbol");
   request.volume = StringToDouble(GetParam(request_body, "volume"));
   request.type = (ENUM_ORDER_TYPE)StringToInteger(GetParam(request_body, "type"));
   request.price = StringToDouble(GetParam(request_body, "price"));
   request.sl = StringToDouble(GetParam(request_body, "sl"));
   request.tp = StringToDouble(GetParam(request_body, "tp"));
   request.magic = (ulong)StringToInteger(GetParam(request_body, "magic"));
   request.comment = GetParam(request_body, "comment");
   request.type_time = (ENUM_ORDER_TYPE_TIME)StringToInteger(GetParam(request_body, "type_time"));
   request.type_filling = (ENUM_ORDER_TYPE_FILLING)StringToInteger(GetParam(request_body, "type_filling"));

   string position_value = GetParam(request_body, "position");
   if(position_value != "")
      request.position = (ulong)StringToInteger(position_value);

   if(request.price <= 0.0)
   {
      MqlTick tick;
      if(SymbolInfoTick(request.symbol, tick))
         request.price = request.type == ORDER_TYPE_BUY ? tick.ask : tick.bid;
   }

   bool sent = OrderSend(request, result);
   result_json = "{"
      + "\"retcode\":" + IntegerToString((int)result.retcode) + ","
      + "\"deal\":" + ULongToJson(result.deal) + ","
      + "\"order\":" + ULongToJson(result.order) + ","
      + "\"volume\":" + DoubleToJson(result.volume) + ","
      + "\"price\":" + DoubleToJson(result.price) + ","
      + "\"bid\":" + DoubleToJson(result.bid) + ","
      + "\"ask\":" + DoubleToJson(result.ask) + ","
      + "\"comment\":\"" + JsonEscape(result.comment) + "\","
      + "\"request_id\":" + IntegerToString((int)result.request_id)
      + "}";

   if(!sent)
      error_message = "OrderSend failed: " + result.comment;
   else if(result.retcode != TRADE_RETCODE_DONE)
   {
      error_message = result.comment;
      return false;
   }

   return sent && result.retcode == TRADE_RETCODE_DONE;
}

bool HandlePositionsGet(const string request_body, string &result_json, string &error_message)
{
   string symbol_filter = GetParam(request_body, "symbol");
   ulong ticket_filter = (ulong)StringToInteger(GetParam(request_body, "ticket"));

   result_json = "[";
   bool first = true;
   int total = PositionsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      if(symbol_filter != "" && symbol != symbol_filter)
         continue;
      if(ticket_filter > 0 && ticket != ticket_filter)
         continue;

      if(!first)
         result_json += ",";
      first = false;

      result_json += "{"
         + "\"ticket\":" + ULongToJson(ticket) + ","
         + "\"time\":" + IntegerToString((int)PositionGetInteger(POSITION_TIME)) + ","
         + "\"type\":" + IntegerToString((int)PositionGetInteger(POSITION_TYPE)) + ","
         + "\"magic\":" + IntegerToString((int)PositionGetInteger(POSITION_MAGIC)) + ","
         + "\"volume\":" + DoubleToJson(PositionGetDouble(POSITION_VOLUME)) + ","
         + "\"price_open\":" + DoubleToJson(PositionGetDouble(POSITION_PRICE_OPEN)) + ","
         + "\"sl\":" + DoubleToJson(PositionGetDouble(POSITION_SL)) + ","
         + "\"tp\":" + DoubleToJson(PositionGetDouble(POSITION_TP)) + ","
         + "\"price_current\":" + DoubleToJson(PositionGetDouble(POSITION_PRICE_CURRENT)) + ","
         + "\"profit\":" + DoubleToJson(PositionGetDouble(POSITION_PROFIT)) + ","
         + "\"symbol\":\"" + JsonEscape(symbol) + "\","
         + "\"comment\":\"" + JsonEscape(PositionGetString(POSITION_COMMENT)) + "\""
         + "}";
   }

   result_json += "]";
   return true;
}

bool HandleHistoryDealsGet(const string request_body, string &result_json, string &error_message)
{
   datetime start_time = (datetime)StringToInteger(GetParam(request_body, "start_time"));
   datetime end_time = (datetime)StringToInteger(GetParam(request_body, "end_time"));

   if(!HistorySelect(start_time, end_time))
   {
      error_message = "HistorySelect failed";
      return false;
   }

   result_json = "[";
   bool first = true;
   int total = HistoryDealsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong deal_ticket = HistoryDealGetTicket(i);
      if(deal_ticket == 0)
         continue;

      if(!first)
         result_json += ",";
      first = false;

      result_json += "{"
         + "\"ticket\":" + ULongToJson(deal_ticket) + ","
         + "\"order\":" + ULongToJson((ulong)HistoryDealGetInteger(deal_ticket, DEAL_ORDER)) + ","
         + "\"time\":" + IntegerToString((int)HistoryDealGetInteger(deal_ticket, DEAL_TIME)) + ","
         + "\"type\":" + IntegerToString((int)HistoryDealGetInteger(deal_ticket, DEAL_TYPE)) + ","
         + "\"magic\":" + IntegerToString((int)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC)) + ","
         + "\"volume\":" + DoubleToJson(HistoryDealGetDouble(deal_ticket, DEAL_VOLUME)) + ","
         + "\"price\":" + DoubleToJson(HistoryDealGetDouble(deal_ticket, DEAL_PRICE)) + ","
         + "\"profit\":" + DoubleToJson(HistoryDealGetDouble(deal_ticket, DEAL_PROFIT)) + ","
         + "\"commission\":" + DoubleToJson(HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION)) + ","
         + "\"swap\":" + DoubleToJson(HistoryDealGetDouble(deal_ticket, DEAL_SWAP)) + ","
         + "\"symbol\":\"" + JsonEscape(HistoryDealGetString(deal_ticket, DEAL_SYMBOL)) + "\","
         + "\"comment\":\"" + JsonEscape(HistoryDealGetString(deal_ticket, DEAL_COMMENT)) + "\""
         + "}";
   }

   result_json += "]";
   return true;
}

bool BuildTerminalInfo(string &result_json, string &error_message)
{
   result_json = "{"
      + "\"connected\":" + BoolToJson((bool)TerminalInfoInteger(TERMINAL_CONNECTED)) + ","
      + "\"trade_allowed\":" + BoolToJson((bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) + ","
      + "\"dlls_allowed\":" + BoolToJson((bool)TerminalInfoInteger(TERMINAL_DLLS_ALLOWED)) + ","
      + "\"tradeapi_disabled\":" + BoolToJson((bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? false : true) + ","
      + "\"build\":" + IntegerToString((int)TerminalInfoInteger(TERMINAL_BUILD)) + ","
      + "\"company\":\"" + JsonEscape(TerminalInfoString(TERMINAL_COMPANY)) + "\","
      + "\"name\":\"" + JsonEscape(TerminalInfoString(TERMINAL_NAME)) + "\","
      + "\"path\":\"" + JsonEscape(TerminalInfoString(TERMINAL_PATH)) + "\","
      + "\"data_path\":\"" + JsonEscape(TerminalInfoString(TERMINAL_DATA_PATH)) + "\""
      + "}";
   return true;
}

bool BuildAccountInfo(string &result_json, string &error_message)
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
   {
      error_message = "Terminal not connected";
      return false;
   }

   result_json = "{"
      + "\"login\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + ","
      + "\"balance\":" + DoubleToJson(AccountInfoDouble(ACCOUNT_BALANCE)) + ","
      + "\"equity\":" + DoubleToJson(AccountInfoDouble(ACCOUNT_EQUITY)) + ","
      + "\"margin\":" + DoubleToJson(AccountInfoDouble(ACCOUNT_MARGIN)) + ","
      + "\"margin_free\":" + DoubleToJson(AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + ","
      + "\"margin_level\":" + DoubleToJson(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL)) + ","
      + "\"profit\":" + DoubleToJson(AccountInfoDouble(ACCOUNT_PROFIT)) + ","
      + "\"currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) + "\","
      + "\"leverage\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LEVERAGE)) + ","
      + "\"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\","
      + "\"company\":\"" + JsonEscape(AccountInfoString(ACCOUNT_COMPANY)) + "\""
      + "}";
   return true;
}

bool BuildSymbolInfo(const string symbol, string &result_json, string &error_message)
{
   if(symbol == "")
   {
      error_message = "Missing symbol";
      return false;
   }
   if(!SymbolSelect(symbol, true))
   {
      error_message = "SymbolSelect failed";
      return false;
   }

   result_json = "{"
      + "\"name\":\"" + JsonEscape(symbol) + "\","
      + "\"description\":\"" + JsonEscape(SymbolInfoString(symbol, SYMBOL_DESCRIPTION)) + "\","
      + "\"point\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_POINT)) + ","
      + "\"digits\":" + IntegerToString((int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ","
      + "\"spread\":" + IntegerToString((int)SymbolInfoInteger(symbol, SYMBOL_SPREAD)) + ","
      + "\"volume_min\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN)) + ","
      + "\"volume_max\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX)) + ","
      + "\"volume_step\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP)) + ","
      + "\"trade_tick_size\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE)) + ","
      + "\"trade_tick_value\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE)) + ","
      + "\"trade_contract_size\":" + DoubleToJson(SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE)) + ","
      + "\"trade_mode\":" + IntegerToString((int)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE))
      + "}";
   return true;
}

bool BuildSymbolTick(const string symbol, string &result_json, string &error_message)
{
   MqlTick tick;
   if(symbol == "")
   {
      error_message = "Missing symbol";
      return false;
   }
   if(!SymbolInfoTick(symbol, tick))
   {
      error_message = "SymbolInfoTick failed";
      return false;
   }

   result_json = "{"
      + "\"time\":" + IntegerToString((int)tick.time) + ","
      + "\"bid\":" + DoubleToJson(tick.bid) + ","
      + "\"ask\":" + DoubleToJson(tick.ask) + ","
      + "\"last\":" + DoubleToJson(tick.last) + ","
      + "\"volume\":" + IntegerToString((int)tick.volume)
      + "}";
   return true;
}

void PostResponse(const string request_id, const bool ok, const string result_json, const string error_message)
{
   string body = "{"
      + "\"request_id\":\"" + JsonEscape(request_id) + "\","
      + "\"ok\":" + BoolToJson(ok) + ","
      + "\"error\":\"" + JsonEscape(error_message) + "\","
      + "\"error_code\":" + IntegerToString(GetLastError()) + ","
      + "\"result\":" + (StringLen(result_json) > 0 ? result_json : "null")
      + "}";

   char payload[];
   char response[];
   string response_headers = "";
   int payload_len = StringToCharArray(body, payload, 0, WHOLE_ARRAY, CP_UTF8);
   if(payload_len > 0)
      ArrayResize(payload, payload_len - 1);
   ResetLastError();
   int status = WebRequest(
      "POST",
      g_base_url + "/response?token=" + BridgeToken,
      "",
      "",
      RequestTimeoutMs,
      payload,
      ArraySize(payload),
      response,
      response_headers
   );
   string response_body = CharArrayToString(response, 0, ArraySize(response), CP_UTF8);
   if(status == -1)
   {
      Print("Bridge response failed. WebRequest error: ", GetLastError(), " body=", body);
      return;
   }

   Print("Bridge response posted. HTTP status=", status, " last_error=", GetLastError(), " response=", response_body, " headers=", response_headers);
}

bool SupportsPeriodS1()
{
#ifdef PERIOD_S1
   return true;
#else
   return false;
#endif
}

bool IsS1Request(const string value)
{
   string normalized = Trim(value);
   StringToUpper(normalized);
   return normalized == "S1" || normalized == "1S";
}

void ResetS1Cache(const string symbol)
{
   g_s1_cache_symbol = symbol;
   ArrayResize(g_s1_cache_rates, 0);
   g_s1_cache_last_tick_msc = 0;
   g_s1_cache_ready = false;
}

double TickToPrice(MqlTick &tick)
{
   if(tick.bid > 0.0)
      return tick.bid;
   if(tick.last > 0.0)
      return tick.last;
   return tick.ask;
}

void TrimS1Cache()
{
   int total = ArraySize(g_s1_cache_rates);
   int max_bars = MathMax(S1CacheMaxBars, 1000);
   if(total <= max_bars)
      return;

   int to_drop = total - max_bars;
   for(int i = 0; i < max_bars; i++)
      g_s1_cache_rates[i] = g_s1_cache_rates[i + to_drop];
   ArrayResize(g_s1_cache_rates, max_bars);
}

void AppendTickToS1Cache(const string symbol, MqlTick &tick)
{
   datetime second_time = (datetime)tick.time;
   double price = TickToPrice(tick);
   if(price <= 0.0 || second_time <= 0)
      return;

   int spread = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   int total = ArraySize(g_s1_cache_rates);
   if(total == 0 || g_s1_cache_rates[total - 1].time != second_time)
   {
      MqlRates bar;
      ZeroMemory(bar);
      bar.time = second_time;
      bar.open = price;
      bar.high = price;
      bar.low = price;
      bar.close = price;
      bar.tick_volume = 1;
      bar.spread = spread;
      bar.real_volume = 0;
      ArrayResize(g_s1_cache_rates, total + 1);
      g_s1_cache_rates[total] = bar;
   }
   else
   {
      if(price > g_s1_cache_rates[total - 1].high)
         g_s1_cache_rates[total - 1].high = price;
      if(price < g_s1_cache_rates[total - 1].low)
         g_s1_cache_rates[total - 1].low = price;
      g_s1_cache_rates[total - 1].close = price;
      g_s1_cache_rates[total - 1].tick_volume++;
      g_s1_cache_rates[total - 1].spread = spread;
   }

   long tick_msc = (long)tick.time_msc;
   if(tick_msc <= 0)
      tick_msc = (long)tick.time * 1000;
   if(tick_msc > g_s1_cache_last_tick_msc)
      g_s1_cache_last_tick_msc = tick_msc;
}

bool RefreshS1Cache(const string symbol)
{
   if(symbol == "")
      return false;

   if(g_s1_cache_symbol != symbol)
      ResetS1Cache(symbol);

   if(!SymbolSelect(symbol, true))
      return false;

   datetime now = TimeCurrent();
   long to_msc = (long)(now + 1) * 1000;
   long from_msc = 0;
   if(g_s1_cache_ready)
      from_msc = g_s1_cache_last_tick_msc + 1;
   else
   {
      long lookback = MathMax((long)S1CacheLookbackSeconds, 300);
      from_msc = (long)(now - (datetime)lookback) * 1000;
   }

   if(from_msc >= to_msc)
      return true;

   MqlTick ticks[];
   ResetLastError();
   int copied = CopyTicksRange(symbol, ticks, COPY_TICKS_ALL, (ulong)from_msc, (ulong)to_msc);
   if(copied < 0)
   {
      Print("S1 cache refresh failed. symbol=", symbol, " error=", GetLastError());
      return false;
   }

   if(copied == 0 && !g_s1_cache_ready)
   {
      // Fallback: prime cache from recent ticks if time-range query returns none.
      copied = CopyTicks(symbol, ticks, COPY_TICKS_ALL, 0, 2000);
      if(copied < 0)
      {
         Print("S1 cache bootstrap failed. symbol=", symbol, " error=", GetLastError());
         return false;
      }
   }

   for(int i = 0; i < copied; i++)
      AppendTickToS1Cache(symbol, ticks[i]);

   if(copied > 0)
   {
      g_s1_cache_ready = true;
      TrimS1Cache();
   }

   if(!g_warned_s1_cache_mode)
   {
      Print("Using custom tick->S1 cache mode (PERIOD_S1 unavailable).");
      g_warned_s1_cache_mode = true;
   }

   return true;
}

int CopyS1RatesFromPosCached(const string symbol, const int start_pos, const int count, MqlRates &rates[])
{
   if(!RefreshS1Cache(symbol))
      return -1;

   int total = ArraySize(g_s1_cache_rates);
   if(total == 0 || count <= 0)
   {
      ArrayResize(rates, 0);
      return 0;
   }

   int end_idx = total - 1 - MathMax(start_pos, 0);
   if(end_idx < 0)
   {
      ArrayResize(rates, 0);
      return 0;
   }

   int start_idx = end_idx - count + 1;
   if(start_idx < 0)
      start_idx = 0;

   int out_count = end_idx - start_idx + 1;
   if(out_count <= 0)
   {
      ArrayResize(rates, 0);
      return 0;
   }

   ArrayResize(rates, out_count);
   for(int i = 0; i < out_count; i++)
      rates[i] = g_s1_cache_rates[start_idx + i];
   return out_count;
}

int CopyS1RatesFromCached(const string symbol, const datetime start_time, const int count, MqlRates &rates[])
{
   if(!RefreshS1Cache(symbol))
      return -1;

   int total = ArraySize(g_s1_cache_rates);
   if(total == 0 || count <= 0)
   {
      ArrayResize(rates, 0);
      return 0;
   }

   ArrayResize(rates, 0);
   int out_count = 0;
   for(int i = 0; i < total && out_count < count; i++)
   {
      if(g_s1_cache_rates[i].time < start_time)
         continue;
      ArrayResize(rates, out_count + 1);
      rates[out_count] = g_s1_cache_rates[i];
      out_count++;
   }
   return out_count;
}

int CopyS1RatesRangeCached(const string symbol, const datetime start_time, const datetime end_time, MqlRates &rates[])
{
   if(!RefreshS1Cache(symbol))
      return -1;

   int total = ArraySize(g_s1_cache_rates);
   if(total == 0)
   {
      ArrayResize(rates, 0);
      return 0;
   }

   ArrayResize(rates, 0);
   int out_count = 0;
   for(int i = 0; i < total; i++)
   {
      datetime t = g_s1_cache_rates[i].time;
      if(t < start_time || t > end_time)
         continue;
      ArrayResize(rates, out_count + 1);
      rates[out_count] = g_s1_cache_rates[i];
      out_count++;
   }
   return out_count;
}

string RatesToJson(MqlRates &rates[], const int count)
{
   string json = "[";
   for(int i = 0; i < count; i++)
   {
      if(i > 0)
         json += ",";
      json += "{"
         + "\"time\":" + IntegerToString((int)rates[i].time) + ","
         + "\"open\":" + DoubleToJson(rates[i].open) + ","
         + "\"high\":" + DoubleToJson(rates[i].high) + ","
         + "\"low\":" + DoubleToJson(rates[i].low) + ","
         + "\"close\":" + DoubleToJson(rates[i].close) + ","
         + "\"tick_volume\":" + IntegerToString((int)rates[i].tick_volume) + ","
         + "\"real_volume\":" + IntegerToString((int)rates[i].real_volume) + ","
         + "\"spread\":" + IntegerToString((int)rates[i].spread)
         + "}";
   }
   json += "]";
   return json;
}

ENUM_TIMEFRAMES ParseTimeframe(const string value)
{
   string normalized = Trim(value);
   StringToUpper(normalized);

#ifdef PERIOD_S1
   if(normalized == "S1" || normalized == "1S")
      return PERIOD_S1;
#else
   if(normalized == "S1" || normalized == "1S")
   {
      if(!g_warned_s1_fallback)
      {
         Print("PERIOD_S1 is not supported by this MT5 build. Falling back to PERIOD_M1.");
         g_warned_s1_fallback = true;
      }
      return PERIOD_M1;
   }
#endif
   if(normalized == "M1")
      return PERIOD_M1;
   if(normalized == "M5")
      return PERIOD_M5;
   if(normalized == "M15")
      return PERIOD_M15;
   if(normalized == "M30")
      return PERIOD_M30;
   if(normalized == "H1")
      return PERIOD_H1;
   if(normalized == "H4")
      return PERIOD_H4;
   if(normalized == "D1")
      return PERIOD_D1;
   if(normalized == "W1")
      return PERIOD_W1;
   if(normalized == "MN1")
      return PERIOD_MN1;

   int raw = (int)StringToInteger(normalized);
   switch(raw)
   {
      case 1: return PERIOD_M1;
      case 5: return PERIOD_M5;
      case 15: return PERIOD_M15;
      case 30: return PERIOD_M30;
      case 60: return PERIOD_H1;
      case 240: return PERIOD_H4;
      case 1440: return PERIOD_D1;
      case 10080: return PERIOD_W1;
      case 43200: return PERIOD_MN1;
   }
   return PERIOD_CURRENT;
}

string GetParam(const string body, const string key)
{
   return GetLineValue(body, "param_" + key);
}

string GetLineValue(const string body, const string key)
{
   string lines[];
   int count = StringSplit(body, '\n', lines);
   string prefix = key + "=";
   for(int i = 0; i < count; i++)
   {
      string line = Trim(lines[i]);
      if(StringFind(line, prefix) == 0)
         return StringSubstr(line, StringLen(prefix));
   }
   return "";
}

string JsonEscape(const string value)
{
   string escaped = value;
   StringReplace(escaped, "\\", "\\\\");
   StringReplace(escaped, "\"", "\\\"");
   StringReplace(escaped, "\r", "\\r");
   StringReplace(escaped, "\n", "\\n");
   StringReplace(escaped, "\t", "\\t");
   return escaped;
}

string Trim(const string value)
{
   string result = value;
   StringTrimLeft(result);
   StringTrimRight(result);
   return result;
}

string DoubleToJson(const double value)
{
   if(value == EMPTY_VALUE)
      return "0";
   return DoubleToString(value, 8);
}

string BoolToJson(const bool value)
{
   return value ? "true" : "false";
}

string ULongToJson(const ulong value)
{
   return (string)value;
}
