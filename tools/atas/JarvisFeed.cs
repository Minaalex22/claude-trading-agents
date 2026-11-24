// JarvisFeed: an ATAS indicator that sends each finished bar's order flow to the ATS Matrix app.
//
// What it sends (POST {Url}/api/atas/feed, header "Authorization: Bearer {Token}"), once per finished bar:
//   symbol, timeframe, open/high/low/close, volume, delta, cumulative delta, buy/sell volume, POC,
//   max/min delta, and big trades (prints at or above "Big trade size").
// It only sends data. It never places, changes or cancels an order.
//
// Install (see README.md next to this file):
//   ATAS > Settings (gear) > "Indicators folder" > copy the compiled JarvisFeed.dll there, or use
//   ATAS's "Strategies & Indicators" development template, paste this file, build, and copy the .dll.
//   Restart ATAS, open a chart, Indicators > JarvisFeed, paste the Url and Token, OK.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;

using ATAS.Indicators;

namespace AtsMatrix
{
    [DisplayName("JarvisFeed (ATS Matrix)")]
    public class JarvisFeed : Indicator
    {
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        private int _lastSent = -1;
        private readonly List<string> _bigTrades = new List<string>();
        private readonly object _lock = new object();

        [Display(Name = "App URL", GroupName = "ATS Matrix", Order = 10)]
        public string Url { get; set; } = "https://ats-matrix-6la7.onrender.com";

        [Display(Name = "Token (same as ATAS_TOKEN on Render)", GroupName = "ATS Matrix", Order = 20)]
        public string Token { get; set; } = "";

        [Display(Name = "Symbol name in the app (blank = chart symbol)", GroupName = "ATS Matrix", Order = 30)]
        public string SymbolName { get; set; } = "";

        [Display(Name = "Big trade size", GroupName = "ATS Matrix", Order = 40)]
        public decimal BigTradeSize { get; set; } = 50m;

        public JarvisFeed() : base(true)
        {
            DenyToChangePanel = true;
            DataSeries[0].IsHidden = true;
        }

        protected override void OnCalculate(int bar, decimal value)
        {
            // a bar is finished when the next one starts; send the finished one once
            var finished = bar - 1;
            if (bar != CurrentBar - 1 || finished < 0 || finished <= _lastSent) return;
            _lastSent = finished;
            if (string.IsNullOrWhiteSpace(Token) || string.IsNullOrWhiteSpace(Url)) return;

            var c = GetCandle(finished);
            decimal cumDelta = 0;  // cumulative delta over the bars loaded on the chart
            for (var i = 0; i <= finished; i++) cumDelta += GetCandle(i).Delta;
            var poc = c.MaxVolumePriceInfo != null ? c.MaxVolumePriceInfo.Price : c.Close;
            string big;
            lock (_lock)
            {
                big = string.Join(",", _bigTrades);
                _bigTrades.Clear();
            }

            var sym = string.IsNullOrWhiteSpace(SymbolName) ? InstrumentInfo?.Instrument ?? "ATAS" : SymbolName;
            var json = new StringBuilder();
            json.Append("{\"symbol\":").Append(Q(sym))
                .Append(",\"timeframe\":").Append(Q(ChartInfo?.TimeFrame ?? ""))
                .Append(",\"bar\":{")
                .Append("\"time\":").Append(Q(c.Time.ToString("o", Inv)))
                .Append(",\"open\":").Append(N(c.Open))
                .Append(",\"high\":").Append(N(c.High))
                .Append(",\"low\":").Append(N(c.Low))
                .Append(",\"close\":").Append(N(c.Close))
                .Append(",\"volume\":").Append(N(c.Volume))
                .Append(",\"delta\":").Append(N(c.Delta))
                .Append(",\"cum_delta\":").Append(N(cumDelta))
                .Append(",\"buy_volume\":").Append(N(c.Ask))
                .Append(",\"sell_volume\":").Append(N(c.Bid))
                .Append(",\"poc\":").Append(N(poc))
                .Append(",\"max_delta\":").Append(N(c.MaxDelta))
                .Append(",\"min_delta\":").Append(N(c.MinDelta))
                .Append("},\"big_trades\":[").Append(big).Append("]}");
            Send(json.ToString());
        }

        protected override void OnNewTrade(MarketDataArg trade)
        {
            if (trade.Volume < BigTradeSize) return;
            var side = trade.Direction == TradeDirection.Buy ? "buy" : "sell";
            lock (_lock)
            {
                if (_bigTrades.Count < 50)
                    _bigTrades.Add("{\"price\":" + N(trade.Price) + ",\"size\":" + N(trade.Volume) + ",\"side\":\"" + side + "\"}");
            }
        }

        private void Send(string body)
        {
            var url = Url.TrimEnd('/') + "/api/atas/feed";
            var token = Token.Trim();
            Task.Run(async () =>
            {
                try
                {
                    using (var req = new HttpRequestMessage(HttpMethod.Post, url))
                    {
                        req.Headers.TryAddWithoutValidation("Authorization", "Bearer " + token);
                        req.Content = new StringContent(body, Encoding.UTF8, "application/json");
                        using (var res = await Http.SendAsync(req).ConfigureAwait(false))
                        {
                            if (!res.IsSuccessStatusCode)
                                System.Diagnostics.Debug.WriteLine("JarvisFeed: app answered " + (int)res.StatusCode);
                        }
                    }
                }
                catch (Exception e)
                {
                    System.Diagnostics.Debug.WriteLine("JarvisFeed: could not reach the app: " + e.Message);
                }
            });
        }

        private static string N(decimal v) => v.ToString(Inv);

        private static string Q(string s) =>
            "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
