package cfsolve

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const testUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0"

// fakeSolver 起一个假的求解服务，返回固定 clearance；calls 记录被调用次数。
func fakeSolver(t *testing.T, calls *int32, body func() map[string]any, status int) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(calls, 1)
		if r.URL.Path == "/stats" || r.URL.Path == "/health" {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "browser": "camoufox"})
			return
		}
		if r.Header.Get("Authorization") != "Bearer test-token" {
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"ok": false, "error_code": "unauthorized", "error": "缺少或错误的 Bearer token", "stage": "admit",
			})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body())
	}))
}

func okBody() map[string]any {
	return map[string]any{
		"ok":           true,
		"solved":       true,
		"cf_clearance": "CLEARANCE",
		"user_agent":   testUA,
		"cookies":      map[string]string{"cf_clearance": "CLEARANCE"},
		"cookie_list": []map[string]any{
			{"name": "cf_clearance", "value": "CLEARANCE", "domain": ".example.com", "path": "/", "expires": 1},
		},
		"expires_in": 1500,
		"expires_at": float64(time.Now().Add(20 * time.Minute).Unix()),
		"egress_id":  "default",
	}
}

func TestGetSendsBearerTokenAndParsesClearance(t *testing.T) {
	var calls int32
	srv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	cl, err := cli.Get(context.Background(), "https://www.example.com/")
	if err != nil {
		t.Fatalf("Get 失败: %v", err)
	}
	if cl.CFClearance != "CLEARANCE" || cl.UserAgent != testUA {
		t.Fatalf("解析结果不对: %+v", cl)
	}
	if len(cl.CookieList) != 1 || cl.CookieList[0].Domain != ".example.com" {
		t.Fatalf("cookie_list 未解析: %+v", cl.CookieList)
	}
}

func TestMissingTokenSurfacesAPIError(t *testing.T) {
	var calls int32
	srv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer srv.Close()

	cli := New(srv.URL) // 不带 token
	_, err := cli.Get(context.Background(), "https://www.example.com/")
	if err == nil {
		t.Fatal("应当返回 401 错误")
	}
	var apiErr *APIError
	if !asAPIError(err, &apiErr) {
		t.Fatalf("错误类型应为 *APIError，实际 %T: %v", err, err)
	}
	if apiErr.StatusCode != http.StatusUnauthorized || apiErr.Code != "unauthorized" {
		t.Fatalf("错误码不对: %+v", apiErr)
	}
}

func TestUnsolvedIsHTTP200WithErrorCode(t *testing.T) {
	var calls int32
	srv := fakeSolver(t, &calls, func() map[string]any {
		return map[string]any{
			"ok": false, "solved": false, "error_code": "unsolved",
			"error": "45s 内未取得 cf_clearance", "stage": "wait", "hint": "该域可能已升级",
		}
	}, http.StatusOK)
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	_, err := cli.Get(context.Background(), "https://www.example.com/")
	var apiErr *APIError
	if !asAPIError(err, &apiErr) {
		t.Fatalf("应返回 *APIError，实际 %T: %v", err, err)
	}
	if apiErr.Code != "unsolved" || apiErr.Hint == "" {
		t.Fatalf("求解失败必须带 error_code 与 hint: %+v", apiErr)
	}
	if !strings.Contains(err.Error(), "建议") {
		t.Fatalf("错误信息应包含可执行建议: %v", err)
	}
}

func TestCacheHitSkipsSecondSolve(t *testing.T) {
	var calls int32
	srv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	ctx := context.Background()
	if _, err := cli.Get(ctx, "https://www.example.com/"); err != nil {
		t.Fatal(err)
	}
	cl2, err := cli.Get(ctx, "https://www.example.com/")
	if err != nil {
		t.Fatal(err)
	}
	if !cl2.Cached {
		t.Fatal("第二次应命中本地缓存")
	}
	if cl2.Elapsed != 0 {
		t.Fatalf("缓存命中时 Elapsed 必须为 0，实际 %v", cl2.Elapsed)
	}
	if n := atomic.LoadInt32(&calls); n != 1 {
		t.Fatalf("缓存命中不应再调求解服务，实际调用 %d 次", n)
	}
}

func TestEgressIDSeparatesCache(t *testing.T) {
	var calls int32
	srv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer srv.Close()

	ctx := context.Background()
	a := New(srv.URL, WithToken("test-token"), WithEgressID("proxy-a"))
	b := New(srv.URL, WithToken("test-token"), WithEgressID("proxy-b"))
	if _, err := a.Get(ctx, "https://www.example.com/"); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Get(ctx, "https://www.example.com/"); err != nil {
		t.Fatal(err)
	}
	if n := atomic.LoadInt32(&calls); n != 2 {
		t.Fatalf("不同出口必须各解一次（否则会把 A 出口的凭证发给 B 出口），实际 %d 次", n)
	}
}

func TestCookieHeaderForFiltersDomainAndPutsClearanceFirst(t *testing.T) {
	cl := &Clearance{
		CFClearance: "C",
		CookieList: []Cookie{
			{Name: "__cf_bm", Value: "bm", Domain: ".example.com"},
			{Name: "zzz", Value: "z", Domain: ".example.com"},
			{Name: "cf_clearance", Value: "C", Domain: ".example.com"},
			{Name: "_ga", Value: "should-be-dropped", Domain: ".other.com"},
		},
	}
	got, err := cl.CookieHeaderFor("www.example.com")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(got, "_ga") {
		t.Fatalf("不该把别的域的 cookie 发出去: %s", got)
	}
	if !strings.HasPrefix(got, "cf_clearance=C") {
		t.Fatalf("cf_clearance 必须排首位: %s", got)
	}
	// 多次调用必须完全一致（原来遍历 map，顺序随机）
	for i := 0; i < 5; i++ {
		again, _ := cl.CookieHeaderFor("www.example.com")
		if again != got {
			t.Fatalf("cookie 头顺序不稳定: %q vs %q", got, again)
		}
	}
}

func TestDoWithHonorsContextDeadline(t *testing.T) {
	var calls int32
	solverSrv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer solverSrv.Close()

	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(2 * time.Second)
		_, _ = w.Write([]byte("late"))
	}))
	defer slow.Close()

	cli := New(solverSrv.URL, WithToken("test-token"))
	cl, err := cli.Get(context.Background(), slow.URL)
	if err != nil {
		t.Fatal(err)
	}
	cl.CookieList = []Cookie{{Name: "cf_clearance", Value: "CLEARANCE", Path: "/"}}

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, slow.URL, nil)

	start := time.Now()
	_, err = cli.DoWith(ctx, req, cl)
	elapsed := time.Since(start)
	if err == nil {
		t.Fatal("ctx 到期应当返回错误")
	}
	if elapsed > 1500*time.Millisecond {
		t.Fatalf("回放没有遵守 ctx 超时（用了 %v），说明绕过了自身 client 配置", elapsed)
	}
}

func TestDoWithSetsFirefoxConsistentHeaders(t *testing.T) {
	var calls int32
	solverSrv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer solverSrv.Close()

	var got http.Header
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		_, _ = w.Write([]byte("ok"))
	}))
	defer target.Close()

	cli := New(solverSrv.URL, WithToken("test-token"))
	cl, err := cli.Get(context.Background(), target.URL)
	if err != nil {
		t.Fatal(err)
	}
	// httptest 的 host 是 127.0.0.1；把 cookie 的 domain 置空表示"不过滤"
	cl.CookieList = []Cookie{{Name: "cf_clearance", Value: "CLEARANCE", Path: "/"}}
	req, _ := http.NewRequest(http.MethodGet, target.URL, nil)
	resp, err := cli.DoWith(context.Background(), req, cl)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()

	if got.Get("User-Agent") != testUA {
		t.Fatalf("回放必须用求解时的 UA，实际 %q", got.Get("User-Agent"))
	}
	if got.Get("Cookie") == "" {
		t.Fatal("回放必须带 Cookie")
	}
	for _, h := range []string{"Upgrade-Insecure-Requests", "Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site"} {
		if got.Get(h) == "" {
			t.Fatalf("缺少 Firefox 侧会发的头 %s（UA 说 Firefox 却没有这些头 = 自相矛盾）", h)
		}
	}
	if got.Get("Accept") == "" || got.Get("Accept-Language") == "" {
		t.Fatal("Accept / Accept-Language 应设置")
	}
}

// asAPIError 是 errors.As 的薄封装，避免测试文件再 import errors 造成噪音。
func asAPIError(err error, target **APIError) bool {
	for e := err; e != nil; {
		if ae, ok := e.(*APIError); ok {
			*target = ae
			return true
		}
		type unwrapper interface{ Unwrap() error }
		u, ok := e.(unwrapper)
		if !ok {
			return false
		}
		e = u.Unwrap()
	}
	return false
}

// 同名 cookie 只能发一个，且保留更具体的 domain（Critic 抓到的 ⑤）。
func TestCookieHeaderDeduplicatesSameNameKeepingMostSpecific(t *testing.T) {
	cl := &Clearance{
		CFClearance: "HOSTONLY",
		CookieList: []Cookie{
			{Name: "cf_clearance", Value: "PARENT", Domain: ".example.com"},
			{Name: "cf_clearance", Value: "HOSTONLY", Domain: "www.example.com"},
			{Name: "__cf_bm", Value: "bm", Domain: ".example.com"},
		},
	}
	got, err := cl.CookieHeaderFor("www.example.com")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(got, "cf_clearance=") != 1 {
		t.Fatalf("同名 cookie 不能发两份: %s", got)
	}
	if !strings.HasPrefix(got, "cf_clearance=HOSTONLY") {
		t.Fatalf("应保留 domain 更具体的那条: %s", got)
	}
	// 顺序必须稳定（比较器改成严格弱序后不再反转）
	for i := 0; i < 10; i++ {
		again, _ := cl.CookieHeaderFor("www.example.com")
		if again != got {
			t.Fatalf("顺序不稳定: %q vs %q", got, again)
		}
	}
}

// 没有任何同域 cookie 时必须报错，而不是发一个空 Cookie 头去撞墙。
func TestCookieHeaderForErrorsWhenNoCookieMatchesHost(t *testing.T) {
	cl := &Clearance{CookieList: []Cookie{{Name: "cf_clearance", Value: "X", Domain: ".other.com"}}}
	if _, err := cl.CookieHeaderFor("www.example.com"); err == nil {
		t.Fatal("应当返回错误")
	}
}

// ── v6.4 C5：want / route / sitekey 透传 ────────────────
//
// 断言的是**真实请求体**（httptest 收下来的 JSON），不是结构体字段：
// 字段加了但没写进请求体的话，编译和"字段存在"的断言都会绿。

func captureSolveBody(t *testing.T, calls *int32) (*httptest.Server, *map[string]any) {
	t.Helper()
	got := map[string]any{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(calls, 1)
		raw := make([]byte, r.ContentLength)
		_, _ = r.Body.Read(raw)
		_ = json.Unmarshal(raw, &got)
		w.Header().Set("Content-Type", "application/json")
		body := okBody()
		body["token"] = "TURNSTILE-TOKEN" // want=turnstile 的产物，见 cfsolve.go 的产物校验
		_ = json.NewEncoder(w).Encode(body)
	}))
	return srv, &got
}

func TestSolveWithOptionsSendsWantRouteSitekey(t *testing.T) {
	var calls int32
	srv, got := captureSolveBody(t, &calls)
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	_, err := cli.SolveWithOptions(context.Background(), "https://example.com/", SolveOptions{
		Want:    "turnstile",
		Route:   "auto",
		Sitekey: "0x4AAAAAAA",
	})
	if err != nil {
		t.Fatalf("SolveWithOptions 失败: %v", err)
	}
	body := *got
	if body["want"] != "turnstile" {
		t.Errorf("want 没有进请求体: %v", body)
	}
	if body["route"] != "auto" {
		t.Errorf("route 没有进请求体: %v", body)
	}
	if body["sitekey"] != "0x4AAAAAAA" {
		t.Errorf("sitekey 没有进请求体: %v", body)
	}
	if body["url"] != "https://example.com/" {
		t.Errorf("url 丢了: %v", body)
	}
}

func TestEmptySolveOptionsOmitFieldsFromJSON(t *testing.T) {
	var calls int32
	srv, got := captureSolveBody(t, &calls)
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	if _, err := cli.Get(context.Background(), "https://example.com/"); err != nil {
		t.Fatalf("Get 失败: %v", err)
	}
	body := *got
	// Get 走默认语义：三个键必须**不存在**（omitempty），否则老服务端会收到陌生字段
	for _, k := range []string{"want", "route", "sitekey"} {
		if _, ok := body[k]; ok {
			t.Errorf("默认请求不该带 %q: %v", k, body)
		}
	}
}

func TestSolveWithOptionsRejectsUnparsableTarget(t *testing.T) {
	cli := New("http://127.0.0.1:1", WithToken("t"))
	if _, err := cli.SolveWithOptions(context.Background(), "not a url", SolveOptions{}); err == nil {
		t.Fatal("不可解析的目标应当直接报错，不发请求")
	}
}

// ── P1 回归（独立审查）：非 clearance 产物的解析与校验 ──────────
//
// 修前：`solve()` 只认 `cf_clearance`，want=turnstile 明明拿到了 token 也直接报错，
// 而 `SolveOptions.Want` 的注释正好承诺了这个用法。

func TestSolveWithOptionsParsesTurnstileToken(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok": true, "solved": true, "want": "turnstile",
			"token": "TURNSTILE-TOKEN", "user_agent": testUA, "expires_in": 300,
			"elapsed": 3.5, "egress_id": "default",
		})
	}))
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	cl, err := cli.SolveWithOptions(context.Background(), "https://example.com/",
		SolveOptions{Want: "turnstile", Sitekey: "0x4AAA"})
	if err != nil {
		t.Fatalf("want=turnstile 不该报错: %v", err)
	}
	if cl.Token != "TURNSTILE-TOKEN" {
		t.Errorf("token 没解析出来: %+v", cl)
	}
	if cl.CFClearance != "" {
		t.Errorf("token 类产物不该有 cf_clearance: %+v", cl)
	}
}

func TestSolveWithOptionsParsesAWSWAFToken(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok": true, "solved": true, "aws_waf_token": "AWT-1", "expires_in": 300,
		})
	}))
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	cl, err := cli.SolveWithOptions(context.Background(), "https://example.com/",
		SolveOptions{Want: "awswaf"})
	if err != nil {
		t.Fatalf("want=awswaf 不该报错: %v", err)
	}
	if cl.AWSWAFToken != "AWT-1" {
		t.Errorf("aws_waf_token 没解析出来: %+v", cl)
	}
}

func TestSolveWithOptionsMissingProductStillFails(t *testing.T) {
	// 服务端说 solved=true 但没给对应产物：必须报错，不能把空值当成功返回。
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "solved": true})
	}))
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	if _, err := cli.SolveWithOptions(context.Background(), "https://example.com/",
		SolveOptions{Want: "turnstile"}); err == nil {
		t.Fatal("缺 token 时必须报错")
	}
	if _, err := cli.Get(context.Background(), "https://example.com/"); err == nil {
		t.Fatal("缺 cf_clearance 时必须报错（默认 want 行为不变）")
	}
}

// roundTripFunc 让测试用函数实现 http.RoundTripper。
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// TestSolveUsesLongLivedClient 钉 P0-01：`/solve` 必须走 240s 的 hc，
// 而不是 5s 的探活 client。用自定义 Transport 断言“哪条 client 发了请求”，
// 不依赖真实延迟，测试保持毫秒级。
func TestSolveUsesLongLivedClient(t *testing.T) {
	body, err := json.Marshal(okBody())
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	hc := &http.Client{Timeout: 240 * time.Second, Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     http.Header{"Content-Type": []string{"application/json"}},
			Body:       io.NopCloser(bytes.NewReader(body)),
			Request:    r,
		}, nil
	})}
	probe := &http.Client{Timeout: 5 * time.Second, Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return nil, fmt.Errorf("5s 探活 client 被用于 %s", r.URL.Path)
	})}
	cli := New("http://solver.invalid", WithToken("test-token"))
	cli.hc = hc
	cli.probe = probe

	if _, err := cli.Get(context.Background(), "https://www.example.com/"); err != nil {
		t.Fatalf("Get 必须走 hc；实际错误: %v", err)
	}
}

// TestAutoNoChallengeReturnsErrNoChallenge 钉 P2-03：route=auto 探测到无盾时，
// 服务端回 `{ok:true, solved:false, no_challenge:true}`。这不是失败，客户端必须
// 返回可识别的 ErrNoChallenge，而不是“没返回 cf_clearance”。
func TestAutoNoChallengeReturnsErrNoChallenge(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok": true, "solved": false, "no_challenge": true, "route": "auto",
		})
	}))
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	_, err := cli.Get(context.Background(), "https://www.example.com/")
	if !errors.Is(err, ErrNoChallenge) {
		t.Fatalf("应返回 ErrNoChallenge，实际: %v", err)
	}
}

// TestColdSolveReturnsDeepCopy 钉 P2-02：冷求解返回的 CookieList/Cookies 不能与缓存共享。
// 调用方改一次返回值，同域第二次 Get 不能看到被污染的值。
func TestColdSolveReturnsDeepCopy(t *testing.T) {
	var calls int32
	srv := fakeSolver(t, &calls, okBody, http.StatusOK)
	defer srv.Close()

	cli := New(srv.URL, WithToken("test-token"))
	first, err := cli.Get(context.Background(), "https://www.example.com/")
	if err != nil {
		t.Fatalf("第一次 Get: %v", err)
	}
	first.CookieList[0].Value = "POISONED"
	first.Cookies["cf_clearance"] = "POISONED"

	second, err := cli.Get(context.Background(), "https://www.example.com/")
	if err != nil {
		t.Fatalf("第二次 Get: %v", err)
	}
	if second.CookieList[0].Value == "POISONED" || second.Cookies["cf_clearance"] == "POISONED" {
		t.Fatalf("冷结果与缓存共享了可变字段：%+v", second)
	}
}

// TestReplayFailsClosedWhenProxyUnparsable 钉 P2-01：登记过出口但 URL 解析失败时必须
// fail closed，不能因为 proxies 为空就悄悄走本机直连。
func TestReplayFailsClosedWhenProxyUnparsable(t *testing.T) {
	cli := New("http://solver.invalid", WithToken("t"), WithEgressProxy("p1", "http://[::bad"))
	_, err := cli.replayClientFor("p1")
	if !errors.Is(err, ErrNoEgressProxy) {
		t.Fatalf("应返回 ErrNoEgressProxy，实际: %v", err)
	}
}
