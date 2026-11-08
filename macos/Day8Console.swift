import AppKit
import SwiftUI

private let support = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/Day8Gateway")
struct Route: Decodable, Identifiable {
    let id: String
    let name: String
    let available: Bool
}

struct Reply: Decodable {
    var domainRules: [DomainRule]?
    var protectedDomains: [String]?
    var revision: String?
    var events: [ConnectionEvent]?
    var activeID: String?
    var activeName: String?
    var routes: [Route]?
    var delays: [String: Int]?
    var minutes: Int?
    var running: Bool?
    var measuredAt: Double?
    var origin: String?
    var probeFailed: Bool?
    struct Probe: Decodable { let online: Bool; let ms: Int? }
    var ok: Bool
    var nodes: [String: Probe]?
    var connections: Int?
    var systemTunnel: String?
    var upload: Int?
    var download: Int?
    var targets: [String]?
    var identities: [String: String]?
    var contexts: [String]?
    var current: String?
    var error: String?
}

func runBridge(_ arguments: [String]) async throws -> Reply {
    try await Task.detached {
        let process = Process()
        process.executableURL = support.appendingPathComponent(".venv/bin/python")
        process.arguments = ["-m", "day8_gateway.desktop"] + arguments
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        process.environment = ["HOME": home, "PYTHONPATH": support.appendingPathComponent("src").path,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin",
            "SSH_AUTH_SOCK": ProcessInfo.processInfo.environment["SSH_AUTH_SOCK"] ?? ""]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return try JSONDecoder().decode(Reply.self, from: data)
    }.value
}

@MainActor private final class Dashboard: ObservableObject {
    @Published var savedRoutes = [Route]()
    @Published var official: [VendorRoute]?
    @Published var officialTime: Date?
    @Published var activeID: String?
    @Published var activeName: String?
    @Published var delays = [String: Int]()
    @Published var routeMessage = ""
    @Published var measuringRoutes = false
    @Published var measuredAt: Date?
    @Published var interval: Int?
    @Published var scheduledRunning = false
    @Published var lastOrigin: String?
    @Published var probeFailed = false
    @Published var snapshot: Reply?
    @Published var checked: Date?
    @Published var busy = false
    @Published var checking = false
    @Published var info = ""
    @Published var exitStatus = "尚未核验"
    @Published var containers = [String]()
    @Published var selected = ""
    var identities = [String: String]()
    var listedContext = ""
    @Published var history = [String: [Int?]]()

    var visibleRoutes: [Route] {
        guard let official else { return savedRoutes }
        let available = Set(savedRoutes.filter(\.available).map(\.id))
        return official.map { Route(id: $0.id, name: $0.name, available: available.contains($0.id)) }
    }

    func readRoutes() async {
        let reply = try? await runBridge(["routes"])
        if let rows = reply?.routes { savedRoutes = rows }
        activeID = reply?.activeID
        activeName = reply?.activeName
    }

    func displaySchedule(_ reply: Reply) {
        guard let minutes = reply.minutes else { return }
        interval = minutes
        scheduledRunning = reply.running ?? false
        measuredAt = reply.measuredAt.map(Date.init(timeIntervalSince1970:))
        delays = reply.delays ?? [:]
        lastOrigin = reply.origin
        probeFailed = reply.probeFailed ?? false
    }

    func readSchedule() async {
        if let reply = try? await runBridge(["probe-status"]), reply.ok { displaySchedule(reply) }
    }

    func setInterval(_ minutes: Int) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        if let reply = try? await runBridge(["probe-interval", String(minutes)]), reply.ok {
            displaySchedule(reply)
        } else { routeMessage = "保存定时间隔失败，请检查登录服务。" }
    }

    func refreshOfficial() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        await readRoutes()
        do {
            official = try await Task.detached { try VendorPanel.read() }.value
            officialTime = Date()
            routeMessage = ""
        } catch {
            routeMessage = (error as? VendorPanel.Failure)?.rawValue ?? "读取官方面板失败。"
        }
    }

    func manageRoutes(_ args: [String]) async {
        guard !busy else { return }
        busy = true
        measuringRoutes = args.first == "route-delays"
        defer { busy = false; measuringRoutes = false }
        routeMessage = measuringRoutes ? "" : "正在验证线路…"
        do {
            let reply = try await runBridge(args)
            if args.first == "route-delays" { displaySchedule(reply) }
            if args.first == "import-routes" && reply.ok { delays = [:]; measuredAt = nil }
            await readRoutes()
            if !reply.ok { routeMessage = "操作失败，请核对线路参数和住宅出口。"; return }
            if args.first == "select-route" {
                routeMessage = "已验证住宅出口，当前线路为 " + (activeName ?? "未知")
                await refresh()
            } else {
                routeMessage = args.first == "route-delays" ? "" : "已更新加密连接参数。"
            }
        } catch { routeMessage = "管理服务未响应，请检查后台服务。" }
    }

    func refresh() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        snapshot = try? await runBridge(["health"])
        checked = Date()
        for name in ["Day8-native", "Residual-egress"] {
            var samples = history[name] ?? []
            samples.append(snapshot?.nodes?[name]?.ms)
            history[name] = Array(samples.suffix(20))
        }
    }

    func action(_ args: [String]) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        info = ""
        if args.first == "verify" { exitStatus = "核验中…" }
        do {
            let result = try await runBridge(args)
            switch args.first {
            case "verify":
                exitStatus = result.ok ? "出口匹配" : "核验失败"
            case "targets":
                listedContext = args[1]
                identities = result.identities ?? [:]
                containers = result.targets ?? []
                selected = containers.first ?? ""
                info = result.ok ? (containers.isEmpty ? "该 Context 没有运行中的 day8-gateway 服务。" : "") : "无法连接该 Docker Context。"
            case "sync-official" where result.error == "NoExportedNode":
                info = "这版 Day8 的本地配置没有可导入的 Muon 节点。当前网关继续使用原节点。"
            case "sync-official", "import", "push":
                info = result.ok ? "配置已生效，ChatGPT 出口核验通过。" : "更新失败。请检查节点文件、容器版本和 vault 是否匹配。"
                await refresh()
            default: break
            }
        } catch {
            if args.first == "verify" { exitStatus = "核验失败" }
            info = "未连接管理服务。请检查本机原生部署和登录服务。"
        }
    }
    func vendor() {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.niceax.d8-desktop") else {
            info = "未找到 Day8 官方桌面客户端。"; return
        }
        NSWorkspace.shared.openApplication(at: url, configuration: .init()) { _, err in
            if err != nil { Task { @MainActor in self.info = "无法前往 Day8 账号与充值。" } }
        }
    }
}

private struct RouteMeasurements: View {
    @ObservedObject var model: Dashboard
    @State private var order: Order = .latency

    private enum Order: String, CaseIterable {
        case latency = "延迟优先", catalog = "目录顺序", failures = "失败优先"
    }
    private let mint = Color(red: 0.48, green: 0.93, blue: 0.81)
    private let teal = Color(red: 0.08, green: 0.54, blue: 0.47)

    private var rows: [Route] { model.visibleRoutes }
    private var successful: [(route: Route, delay: Int)] {
        rows.compactMap { row in
            guard let ms = model.delays[row.id], ms >= 0 else { return nil }
            return (row, ms)
        }
    }
    private var sortedRows: [Route] {
        guard order != .catalog else { return rows }
        func rank(_ id: String) -> (Int, Int) {
            guard let ms = model.delays[id] else { return (2, 0) }
            if ms < 0 { return (order == .failures ? 0 : 1, 0) }
            return (order == .failures ? 1 : 0, ms)
        }
        return rows.enumerated().sorted { left, right in
            let a = rank(left.element.id), b = rank(right.element.id)
            return a == b ? left.offset < right.offset : a < b
        }.map(\.element)
    }

    private func metric(_ title: String, value: String, note: String, icon: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(title, systemImage: icon).font(.subheadline.weight(.medium)).foregroundStyle(mint)
            Text(value).font(.system(size: 25, weight: .semibold, design: .rounded)).monospacedDigit()
                .foregroundStyle(.white).lineLimit(1).minimumScaleFactor(0.7)
            Text(note).font(.caption).foregroundStyle(Color.white.opacity(0.85)).lineLimit(1)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private var overview: some View {
        let best = successful.min { $0.delay < $1.delay }
        let active = model.activeID.flatMap { model.delays[$0] }
        let measured = rows.compactMap { model.delays[$0.id] }
        let failed = measured.filter { $0 < 0 }.count
        return HStack(spacing: 20) {
            metric("当前线路", value: model.activeName ?? "未知",
                   note: active.map { $0 >= 0 ? "HTTPS  \($0) ms" : "HTTPS 探测失败" } ?? "等待 HTTPS 测速",
                   icon: "antenna.radiowaves.left.and.right")
            Rectangle().fill(.white.opacity(0.20)).frame(width: 1)
            metric("最低延迟", value: best.map { "\($0.delay) ms" } ?? "—",
                   note: best?.route.name ?? "暂无成功样本", icon: "bolt.fill")
            Rectangle().fill(.white.opacity(0.20)).frame(width: 1)
            metric("探测成功", value: measured.isEmpty ? "—" : "\(successful.count) / \(measured.count)",
                   note: measured.isEmpty ? "共 \(rows.count) 条线路" : "\(failed) 条失败 · \(rows.count - measured.count) 条未测",
                   icon: "checkmark.circle")
        }
        .padding(.horizontal, 20).padding(.vertical, 15)
        .frame(height: 98)
        .background(LinearGradient(colors: [Color(red: 0.07, green: 0.18, blue: 0.22),
                                             Color(red: 0.09, green: 0.29, blue: 0.29)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing),
                    in: RoundedRectangle(cornerRadius: 13))
    }

    private func dataRow(_ row: Route, maxDelay: Int) -> some View {
        let current = model.activeID == row.id
        let ms = model.delays[row.id]
        let official = model.official?.first { $0.id == row.id }?.latency
        return HStack(spacing: 12) {
            HStack(spacing: 7) {
                Image(systemName: current ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(current ? teal : Color.secondary.opacity(0.45))
                    .accessibilityHidden(true)
                Text(row.name).fontWeight(current ? .semibold : .regular).lineLimit(1)
            }.frame(maxWidth: .infinity, alignment: .leading)
            HStack(spacing: 6) {
                let good = ms.map { $0 >= 0 } ?? false
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.secondary.opacity(0.12))
                        if good, let ms {
                            Capsule().fill(current ? teal : Color.accentColor.opacity(0.70))
                                .frame(width: max(2, geo.size.width * CGFloat(ms) / CGFloat(maxDelay)))
                        }
                    }
                }.frame(width: 58, height: 4).accessibilityHidden(true)
                Text(ms.map { $0 < 0 ? "失败" : "\($0) ms" } ?? "未测")
                    .monospacedDigit().foregroundStyle(ms == nil ? Color.secondary : (good ? Color.primary : Color.red))
                    .frame(width: 68, alignment: .trailing)
            }.frame(width: 132, alignment: .trailing)
            Text(official.map { "\($0) ms" } ?? "—").monospacedDigit().foregroundStyle(.secondary)
                .frame(width: 74, alignment: .trailing)
            Group {
                if current {
                    Text("使用中").font(.caption.weight(.semibold)).foregroundStyle(teal)
                } else if row.available {
                    Button("切换") { Task { await model.manageRoutes(["select-route", row.id]) } }
                        .controlSize(.small).disabled(model.busy)
                        .accessibilityLabel("切换到" + row.name)
                } else { Text("不可切换").font(.caption).foregroundStyle(.secondary).help("缺少连接参数或线路维护中") }
            }.frame(width: 62, alignment: .trailing)
        }
        .padding(.horizontal, 14).frame(height: 35)
        .background(current ? teal.opacity(0.10) : Color.clear)
    }

    var body: some View {
        let maximum = max(1, successful.map(\.delay).max() ?? 1)
        VStack(alignment: .leading, spacing: 12) {
            overview
            HStack(spacing: 9) {
                Text("线路列表").font(.headline)
                Text("\(rows.count)").monospacedDigit().font(.caption).foregroundStyle(.secondary)
                Spacer()
                Picker("排序", selection: $order) {
                    ForEach(Order.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }.labelsHidden().fixedSize()
                Button("刷新官方列表") { Task { await model.refreshOfficial() } }.disabled(model.busy)
                Button { Task { await model.manageRoutes(["route-delays"]) } } label: {
                    Label((model.measuringRoutes || model.scheduledRunning) ? "测速中…" : "全部测速", systemImage: "waveform.path.ecg")
                }.buttonStyle(.borderedProminent).disabled(model.busy || model.scheduledRunning)
            }
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Text("线路").frame(maxWidth: .infinity, alignment: .leading)
                    Text("HTTPS 延迟").frame(width: 132, alignment: .trailing)
                    Text("官方延迟").frame(width: 74, alignment: .trailing)
                    Text("操作").frame(width: 62, alignment: .trailing)
                }.font(.caption.weight(.medium)).foregroundStyle(.secondary)
                    .padding(.horizontal, 14).frame(height: 30)
                    .background(Color(nsColor: .controlBackgroundColor))
                Divider()
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(sortedRows) { route in
                            dataRow(route, maxDelay: maximum)
                            Divider().padding(.leading, 14)
                        }
                        if rows.isEmpty { Text("暂无线路").foregroundStyle(.secondary).padding() }
                    }
                }
            }
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(Color.secondary.opacity(0.2)))
            HStack {
                if model.measuringRoutes || model.scheduledRunning { ProgressView().controlSize(.small); Text("正在探测 · 最多 4 条并发") }
                else if model.probeFailed { Text("上次探测未完成").foregroundStyle(.orange) }
                else if let time = model.measuredAt {
                    Text((model.lastOrigin == "scheduled" ? "后台测速 " : "手动测速 ") + time.formatted(date: .abbreviated, time: .standard))
                }
                Spacer()
                Menu {
                    ForEach([0, 5, 15, 30, 60], id: \.self) { minutes in
                        Button {
                            Task { await model.setInterval(minutes) }
                        } label: {
                            let title = minutes == 0 ? "关闭" : "每 " + String(minutes) + " 分钟"
                            if model.interval == minutes { Label(title, systemImage: "checkmark") }
                            else { Text(title) }
                        }
                    }
                } label: {
                    Label(model.interval.map { $0 == 0 ? "定时测速已关闭" : "每 " + String($0) + " 分钟测速" } ?? "后台定时测速",
                          systemImage: "clock")
                }.fixedSize().disabled(model.busy)
                Text(model.officialTime.map { "官方列表 " + $0.formatted(date: .omitted, time: .standard) } ?? "本机加密目录")
            }.font(.caption).foregroundStyle(.secondary)
            if !model.routeMessage.isEmpty { Text(model.routeMessage).font(.callout).textSelection(.enabled) }
        }
        .task {
            while !Task.isCancelled {
                await model.readSchedule()
                do { try await Task.sleep(nanoseconds: 5_000_000_000) } catch { return }
            }
        }
    }
}

private struct Panel: View {
    @StateObject private var model = Dashboard()
    @AppStorage("dockerContext") private var context = ""
    @State private var contexts = [String]()
    @State private var tab = 0
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "point.3.connected.trianglepath.dotted").foregroundStyle(.tint)
                Text("Day8 Gateway").font(.title2.bold())
                Spacer()
                if model.checking || model.busy { ProgressView().controlSize(.small) }
            }
            Picker("视图", selection: $tab) {
                Text("线路监测").tag(0)
                Text("Day8 线路").tag(3)
                Text("域名路由").tag(4)
                Text("连接日志").tag(5)
                Text("配置与账户").tag(1)
                Text("Linux 容器").tag(2)
            }.pickerStyle(.segmented).labelsHidden()
            if tab == 0 {
                if let device = model.snapshot?.systemTunnel {
                    Label(device == "utun63" ? "整机 TUN 已接管 · " + device : "整机路由待检查 · " + device,
                          systemImage: device == "utun63" ? "network" : "exclamationmark.triangle")
                }
                ForEach([("Day8-native", "Day8", "第一跳 · Muon TCP"), ("Residual-egress", "1024proxy", "第二跳 · 经 Day8 的住宅出口")], id: \.0) { name, title, subtitle in
                    GroupBox {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) { Text(name == "Day8-native" ? "Day8 · " + (model.activeName ?? "线路未知") : title).font(.headline); Text(subtitle).foregroundStyle(.secondary) }
                            Spacer()
                            let probe = model.snapshot?.nodes?[name]
                            HStack(alignment: .bottom, spacing: 2) {
                                ForEach(Array((model.history[name] ?? []).enumerated()), id: \.offset) { item in
                                    Rectangle().fill(item.element == nil ? Color.red : Color.teal)
                                        .frame(width: 4, height: item.element.map { min(26, max(4, CGFloat($0) / 25)) } ?? 3)
                                }
                            }.accessibilityHidden(true)
                            Circle().fill(probe.map { $0.online ? Color.green : Color.red } ?? Color.gray).frame(width: 8, height: 8)
                            Text(probe?.ms.map { "\($0) ms" } ?? (probe == nil ? "未知" : "探测失败")).monospacedDigit()
                        }.padding(6)
                    }
                }
                HStack { Text("ChatGPT 住宅出口"); Spacer(); Text(model.exitStatus); Button("核验出口") { Task { await model.action(["verify"]) } }.disabled(model.busy) }
                Divider()
                HStack {
                    if let count = model.snapshot?.connections { Text("活跃连接  \(count)").monospacedDigit() }
                    Spacer()
                    if let date = model.checked { Text(date, style: .time).foregroundStyle(.secondary) }
                    Button("立即测速") { Task { await model.refresh() } }.disabled(model.checking)
                }
                if model.snapshot?.nodes == nil { Text("管理服务未就绪，当前没有测速结果。").foregroundStyle(.secondary) }
            } else if tab == 4 {
                DomainRulesView()
            } else if tab == 5 {
                ConnectionEventsView()
            } else if tab == 3 {
                RouteMeasurements(model: model)
            } else if tab == 1 {
                Text("Day8 官方账户").font(.headline)
                Text("节点在官方客户端内刷新。充值入口位于「账号 → 购买会员」。").foregroundStyle(.secondary)
                Button("前往 Day8 账号与充值") { model.vendor() }
                Divider()
                Text("更新网关节点").font(.headline)
                Text("选择 Muon 节点 JSON 或本项目的加密节点包。验证住宅出口后保存。").foregroundStyle(.secondary)
                Button("读取官方客户端已刷新的配置") { Task { await model.action(["sync-official"]) } }.disabled(model.busy)
                Button("导入账号线路列表 JSON…") {
                    let panel = NSOpenPanel()
                    if panel.runModal() == .OK, let url = panel.url {
                        tab = 3
                        Task { await model.manageRoutes(["import-routes", url.path]) }
                    }
                }.disabled(model.busy)
                Button("选择更新文件…") {
                    let panel = NSOpenPanel()
                    panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK, let url = panel.url { Task { await model.action(["import", url.path]) } }
                }.disabled(model.busy)
            } else {
                Text("更新指定 Linux 网关").font(.headline)
                TextField("Docker Context", text: Binding(get: { context }, set: { value in
                    context = value
                    model.containers = []
                    model.selected = ""
                    model.identities = [:]
                })).disabled(model.busy)
                if !contexts.isEmpty {
                    Menu("选择已有 Context") {
                        ForEach(contexts, id: \.self) { name in
                            Button(name) { context = name; model.selected = ""; model.containers = [] }
                        }
                    }.disabled(model.busy)
                }
                Button("查找容器") { Task { await model.action(["targets", context]) } }.disabled(model.busy)
                if !model.containers.isEmpty {
                    Picker("目标容器", selection: $model.selected) { ForEach(model.containers, id: \.self) { Text($0) } }
                    Button("将当前 Day8 配置应用到此容器") {
                        if context == model.listedContext, let id = model.identities[model.selected] {
                            Task { await model.action(["push", context, id]) }
                        }
                    }.disabled(model.busy || model.selected.isEmpty)
                }
            }
            if (tab == 1 || tab == 2) && !model.info.isEmpty { Text(model.info).font(.callout).textSelection(.enabled) }
            if tab < 3 { Spacer(minLength: 0) }
        }
        .padding(22).frame(minWidth: tab >= 4 ? 790 : 660, minHeight: tab >= 3 ? 510 : 355)
        .task {
            await model.readRoutes()
            if let reply = try? await runBridge(["contexts"]) {
                contexts = reply.contexts ?? []
                if context.isEmpty { context = reply.current ?? "default" }
            }
            while !Task.isCancelled {
                if tab == 0 { await model.refresh() }
                if tab == 0 || tab == 3 { await model.readRoutes() }
                do { try await Task.sleep(nanoseconds: 30_000_000_000) } catch { return }
            }
        }
    }
}
@main struct Day8Console: App {
    @Environment(\.openWindow) private var openWindow
    var body: some Scene {
        Window("Day8 Gateway", id: "gateway") { Panel() }
        MenuBarExtra("Day8 Gateway", systemImage: "network") {
            Button("打开管理窗口") {
                openWindow(id: "gateway")
                NSApp.activate(ignoringOtherApps: true)
            }
            Button("隐藏到后台") { NSApp.hide(nil) }
            Divider()
            Button("退出管理界面（网关继续运行）") { NSApp.terminate(nil) }
        }
    }
}
