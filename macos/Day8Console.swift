import AppKit
import SwiftUI

private let support = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/Day8Gateway")
private struct Reply: Decodable {
    struct Probe: Decodable { let online: Bool; let ms: Int? }
    var ok: Bool
    var nodes: [String: Probe]?
    var connections: Int?
    var upload: Int?
    var download: Int?
    var targets: [String]?
    var identities: [String: String]?
    var contexts: [String]?
    var current: String?
    var error: String?
}

private func runBridge(_ arguments: [String]) async throws -> Reply {
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
                exitStatus = result.ok ? "三次均匹配" : "核验失败"
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
                Text("配置与账户").tag(1)
                Text("Linux 容器").tag(2)
            }.pickerStyle(.segmented).labelsHidden()
            if tab == 0 {
                ForEach([("Day8-native", "Day8", "第一跳 · Muon TCP"), ("Residual-egress", "1024proxy", "第二跳 · 经 Day8 的住宅出口")], id: \.0) { name, title, subtitle in
                    GroupBox {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) { Text(title).font(.headline); Text(subtitle).foregroundStyle(.secondary) }
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
                    if let count = model.snapshot?.connections { Text("Clash 活跃连接  \(count)").monospacedDigit() }
                    Spacer()
                    if let date = model.checked { Text(date, style: .time).foregroundStyle(.secondary) }
                    Button("立即测速") { Task { await model.refresh() } }.disabled(model.checking)
                }
                if model.snapshot?.nodes == nil { Text("管理服务未就绪，当前没有测速结果。").foregroundStyle(.secondary) }
            } else if tab == 1 {
                Text("Day8 官方账户").font(.headline)
                Text("节点在官方客户端内刷新。充值入口位于「账号 → 购买会员」。").foregroundStyle(.secondary)
                Button("前往 Day8 账号与充值") { model.vendor() }
                Divider()
                Text("更新网关节点").font(.headline)
                Text("选择 Muon 节点 JSON 或本项目的加密节点包。验证住宅出口后保存。").foregroundStyle(.secondary)
                Button("读取官方客户端已刷新的配置") { Task { await model.action(["sync-official"]) } }.disabled(model.busy)
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
            if tab != 0 && !model.info.isEmpty { Text(model.info).font(.callout).textSelection(.enabled) }
            Spacer(minLength: 0)
        }
        .padding(22).frame(minWidth: 490, minHeight: 355)
        .task {
            if let reply = try? await runBridge(["contexts"]) {
                contexts = reply.contexts ?? []
                if context.isEmpty { context = reply.current ?? "default" }
            }
            while !Task.isCancelled {
                if tab == 0 { await model.refresh() }
                do { try await Task.sleep(nanoseconds: 30_000_000_000) } catch { return }
            }
        }
    }
}
@main struct Day8Console: App {
    var body: some Scene { WindowGroup("Day8 Gateway") { Panel() } }
}
