// JarvisFeed: an ATAS indicator that sends each finished bar's order flow to the ATS Matrix app.
//
// What it sends (POST {Url}/api/atas/feed, header "Authorization: Bearer {Token}"), once per finished bar:
//   symbol, timeframe, open/high/low/close, volume, delta, cumulative delta, buy/sell volume, POC,
//   max/min delta, and big trades (prints at or above "Big trade size").
// It only sends data. It never places, changes or cancels an order.
//
// Install: the app's One account page gives a one-line installer (Mac Terminal or Windows PowerShell). It builds
// this file against your own ATAS, copies JarvisFeed.dll into the ATAS indicators folder and writes the app URL and
// token to ~/.jarvisfeed/config, so the indicator needs no settings: open a chart, Indicators > JarvisFeed, OK.
// Typing a URL or token into the indicator's settings still works and wins over the config file.

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
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

        // url and token the installer wrote to ~/.jarvisfeed/config (read once)
        private static readonly Lazy<string[]> Saved = new Lazy<string[]>(LoadConfig);

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
            if (string.IsNullOrEmpty(AppToken()) || string.IsNullOrEmpty(AppUrl())) return;

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
            var url = AppUrl().TrimEnd('/') + "/api/atas/feed";
            var token = AppToken();
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

        private string AppToken() =>
            !string.IsNullOrWhiteSpace(Token) ? Token.Trim() : (Saved.Value[1] ?? "");

        private string AppUrl()
        {
            // the installer's URL wins over the built-in default, a URL typed into the settings wins over both
            const string builtIn = "https://ats-matrix-6la7.onrender.com";
            var typed = (Url ?? "").Trim();
            if (typed.Length > 0 && typed != builtIn) return typed;
            return !string.IsNullOrEmpty(Saved.Value[0]) ? Saved.Value[0] : (typed.Length > 0 ? typed : builtIn);
        }

        private static string[] LoadConfig()
        {
            var found = new string[2];
            try
            {
                var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                var path = Path.Combine(home, ".jarvisfeed", "config");
                if (!File.Exists(path)) return found;
                foreach (var line in File.ReadAllLines(path))
                {
                    var i = line.IndexOf('=');
                    if (i <= 0) continue;
                    var key = line.Substring(0, i).Trim();
                    var val = line.Substring(i + 1).Trim();
                    if (key == "url") found[0] = val;
                    else if (key == "token") found[1] = val;
                }
            }
            catch (Exception e)
            {
                System.Diagnostics.Debug.WriteLine("JarvisFeed: could not read ~/.jarvisfeed/config: " + e.Message);
            }
            return found;
        }

        private static string N(decimal v) => v.ToString(Inv);

        private static string Q(string s) =>
            "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
