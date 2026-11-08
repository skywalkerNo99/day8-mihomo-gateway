import SwiftUI

struct DomainRule: Decodable {
    let kind: String
    let value: String
    let target: String
}

struct ConnectionEvent: Decodable, Identifiable {
    let id: Int
    let at: Double
    let host: String
    let port: Int
    let network: String
    let route: String
    let matched: String?
    let outcome: String?
}

private let targets = ["Day8-native", "Residual-egress", "DIRECT", "REJECT"]
private let kinds = ["exact", "suffix", "keyword"]
private func label(_ key: String) -> String {
    switch key {
    case "Day8-native": return "Day8"
    case "Residual-egress": return "Day8 → 1024"
    case "DIRECT": return "DIRECT · 直连"
    case "REJECT": return "拒绝"
    case "exact": return "精确域名"
    case "suffix": return "域名后缀"
    case "keyword": return "关键词"
    default: return key
    }
}

struct DomainRulesView: View {
    @State private var rows = [DomainRule]()
    @State private var defaults = [String]()
    @State private var revision = ""
    @State private var editing = -1
    @State private var value = ""
    @State private var kind = "suffix"
    @State private var target = "Residual-egress"
    @State private var busy = false
    @State private var message = ""
    @State private var builtin = false
    @State private var query = ""

    private func reset() {
        editing = -1; value = ""; kind = "suffix"; target = "Residual-egress"
    }

    @MainActor private func request(_ arguments: [String]) async {
        busy = true
        defer { busy = false }
        do {
            let reply = try await runBridge(arguments)
            guard reply.ok, let list = reply.domainRules, let version = reply.revision else {
                message = reply.error ?? "请检查单 App 登录服务"
                return
            }
            rows = list
            revision = version
            defaults = reply.protectedDomains ?? []
            reset()
            message = arguments[0] == "domain-rules" ? "" : "已保存，新连接按当前规则分流"
        } catch { message = "无法读取分流服务" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("规则集", selection: $builtin) {
                Text("自定义规则 (\(rows.count))").tag(false)
                Text("内置住宅域名 (\(defaults.count))").tag(true)
            }.pickerStyle(.segmented).labelsHidden()
            if builtin {
                TextField("筛选内置域名", text: $query)
                List(defaults.filter { query.isEmpty || $0.localizedCaseInsensitiveContains(query) }, id: \.self) {
                    Text($0).textSelection(.enabled)
                }
            } else {
                Text("公网 UDP 拒绝和住宅规则优先，其次按顺序匹配自定义规则。其余域名中，.cn 或解析 IP 属于中国大陆的走 DIRECT，其他走 Day8。")
                    .font(.callout).foregroundStyle(.secondary)
                List {
                    ForEach(rows.indices, id: \.self) { i in
                        let r = rows[i]
                        HStack {
                            Text(label(r.kind)).foregroundStyle(.secondary)
                            Text(r.value).textSelection(.enabled)
                            Spacer()
                            Text(label(r.target))
                            Button("编辑") { editing = i; value = r.value; kind = r.kind; target = r.target; message = "" }
                        }
                    }
                }.overlay { if rows.isEmpty { Text("尚无自定义规则").foregroundStyle(.secondary) } }
                HStack {
                    Picker("匹配", selection: $kind) { ForEach(kinds, id: \.self) { Text(label($0)).tag($0) } }
                        .labelsHidden().fixedSize()
                    TextField(kind == "keyword" ? "域名包含的关键词" : "例如 example.com", text: $value)
                    Picker("线路", selection: $target) { ForEach(targets, id: \.self) { Text(label($0)).tag($0) } }
                        .labelsHidden().fixedSize()
                }
                HStack {
                    Button(editing < 0 ? "添加并生效" : "保存修改") {
                        Task { await request(["change-domain-rule", "save", revision, String(editing), kind, value, target]) }
                    }.disabled(value.trimmingCharacters(in: .whitespaces).isEmpty || revision.isEmpty)
                    if editing >= 0 {
                        Button("上移") { Task { await request(["change-domain-rule", "up", revision, String(editing)]) } }
                            .disabled(editing == 0)
                        Button("下移") { Task { await request(["change-domain-rule", "down", revision, String(editing)]) } }
                            .disabled(editing == rows.count - 1)
                        Button("取消编辑") { reset() }
                        Button("删除规则", role: .destructive) {
                            Task { await request(["change-domain-rule", "delete", revision, String(editing)]) }
                        }
                    }
                    Spacer()
                    Button("重新载入") { Task { await request(["domain-rules"]) } }
                }
            }
            if !message.isEmpty { Text(message).font(.callout) }
        }.disabled(busy).task { await request(["domain-rules"]) }
    }
}

struct ConnectionEventsView: View {
    @State private var query = ""
    @State private var paused = false
    @State private var rows = [ConnectionEvent]()
    @State private var error = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                TextField("筛选域名，如 openai 或 *.example.com", text: $query)
                Button(paused ? "继续刷新" : "暂停刷新") { paused.toggle() }
            }
            Text("最近 1,500 条分流记录 · 域名包含匹配，支持 * 和 ? · 当前匹配 \(rows.count) 条")
                .font(.caption).foregroundStyle(.secondary)
            Table(rows) {
                TableColumn("时间") { row in Text(Date(timeIntervalSince1970: row.at), style: .time) }.width(82)
                TableColumn("域名") { row in Text(row.host).textSelection(.enabled).help(row.host) }
                TableColumn("端口") { row in Text(String(row.port)) }.width(48)
                TableColumn("协议", value: \.network).width(43)
                TableColumn("出口") { row in Text(label(row.route)) }.width(105)
                TableColumn("结果") { row in
                    Text(row.outcome == "failed" ? "失败" : row.route == "REJECT" ? "拒绝" : "已分流")
                }.width(52)
                TableColumn("命中规则") { row in Text(row.matched ?? "—").help(row.matched ?? "") }
            }.overlay {
                if error { Text("无法读取分流日志").padding().background(.regularMaterial) }
                else if rows.isEmpty { Text("暂无匹配记录").foregroundStyle(.secondary) }
            }
        }.task(id: [query, String(paused)]) {
            do { try await Task.sleep(nanoseconds: 180_000_000) } catch { return }
            repeat {
                let reply = try? await runBridge(["domain-events", query])
                guard !Task.isCancelled else { return }
                error = reply?.ok != true
                rows = reply?.events ?? []
                if paused { return }
                do { try await Task.sleep(nanoseconds: 2_000_000_000) } catch { return }
            } while !Task.isCancelled
        }
    }
}
