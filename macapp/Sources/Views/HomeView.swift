import SwiftUI
import XiJianKit

/// 首页：卡片式布局
/// - 功能入口卡片（对话、角色、世界、资源包、记忆、设置）
/// - 最近对话角色（按最后对话时间排序，展示图片、名字、描述，悬停可进入聊天、发起语音通话）
/// - 固定收藏的角色卡片（来自导入的角色，用户可在角色详情页切换）
struct HomeView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @Environment(UserProfileSettings.self) private var profile
    @State private var appVM = AppViewModel.shared
    @State private var characterVM = CharacterViewModel()
    @State private var pinnedCharacterIDs: Set<String> = []
    @State private var isLoadingCharacters = false
    @State private var recentCharacterTimes: [String: TimeInterval] = [:]
    @State private var showCallSheet = false
    @State private var voiceCallVM: VoiceCallViewModel?
    @State private var selectedCharacterForCall: CharacterInfo?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: XJSpacing.xl) {
                // 顶部问候区
                greetingSection
                    .xjFadeUp()

                // 功能入口卡片网格
                VStack(alignment: .leading, spacing: XJSpacing.md) {
                    HStack {
                        Text(loc("功能"))
                            .font(.title2.bold())
                            .foregroundStyle(.primary)
                        Spacer()
                    }
                    .xjFadeUp(delay: 0.1)

                    LazyVGrid(columns: gridColumns, spacing: XJSpacing.md) {
                        ForEach(FunctionCard.allCases) { card in
                            FunctionCardView(card: card) {
                                handleFunctionTap(card)
                            }
                            .xjFadeUp(delay: 0.15 + Double(card.rawValue) * 0.05)
                        }
                    }
                }

                // 最近对话角色（按最后对话时间排序）
                if !recentCharacters.isEmpty {
                    recentCharactersSection
                        .xjFadeUp(delay: 0.2)
                }

                // 固定收藏的角色
                if !pinnedCharacters.isEmpty {
                    pinnedCharactersSection
                        .xjFadeUp(delay: 0.3)
                } else if !isLoadingCharacters && !characterVM.characters.isEmpty {
                    // 有角色但没固定任何角色：引导固定
                    emptyPinnedSection
                        .xjFadeUp(delay: 0.3)
                }
            }
            .padding(.horizontal, XJSpacing.xl)
            .padding(.vertical, XJSpacing.lg)
        }
        .background(BackgroundLayerView().opacity(0.3))
        .task {
            await loadData()
        }
        .onChange(of: characterVM.characters) { _, _ in
            Task { await loadData() }
        }
        .sheet(isPresented: $showCallSheet) {
            if let vm = voiceCallVM {
                VoiceCallView(viewModel: vm)
            }
        }
    }

    // MARK: - 常量

    private let gridColumns = [
        GridItem(.flexible(), spacing: XJSpacing.md),
        GridItem(.flexible(), spacing: XJSpacing.md)
    ]

    // MARK: - 子视图

    private var greetingSection: some View {
        VStack(alignment: .leading, spacing: XJSpacing.xs) {
            Text(greetingText)
                .font(.system(size: 34, weight: .bold, design: .rounded))
                .foregroundStyle(.primary)
                .tracking(-0.02)
            Text(loc("从这里开始，或继续未完的故事"))
                .font(.body)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var greetingText: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let name = profile.userName.isEmpty ? loc("旅人") : profile.userName
        switch hour {
        case 5..<11: return loc("早安，%@", name)
        case 11..<13: return loc("中午好，%@", name)
        case 13..<18: return loc("下午好，%@", name)
        case 18..<22: return loc("晚上好，%@", name)
        default: return loc("夜深了，%@", name)
        }
    }

    /// 最近对话角色区块
    private var recentCharactersSection: some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            HStack {
                Text(loc("最近对话"))
                    .font(.title2.bold())
                    .foregroundStyle(.primary)
                Spacer()
                Button {
                    appVM.selectedTab = .characters
                } label: {
                    Label(loc("查看全部"), systemImage: "chevron.right")
                        .font(.caption)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: XJSpacing.md) {
                    ForEach(recentCharacters) { character in
                        RecentCharacterCard(
                            character: character,
                            lastChatTime: recentCharacterTimes[character.id],
                            onChat: {
                                appVM.selectedTab = .chat
                                appVM.selectedCharacterID = character.id
                            },
                            onCall: {
                                selectedCharacterForCall = character
                                startVoiceCall(with: character)
                            }
                        )
                        .xjFadeUp(delay: 0.25)
                    }
                }
                .padding(.horizontal, -XJSpacing.md)
            }
        }
    }

    /// 固定收藏角色区块
    private var pinnedCharactersSection: some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            HStack {
                Text(loc("我的角色"))
                    .font(.title2.bold())
                    .foregroundStyle(.primary)
                Spacer()
                Button {
                    appVM.selectedTab = .characters
                } label: {
                    Label(loc("查看全部"), systemImage: "chevron.right")
                        .font(.caption)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: XJSpacing.md) {
                    ForEach(pinnedCharacters) { character in
                        PinnedCharacterCard(character: character) {
                            appVM.selectedTab = .characters
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                                characterVM.detail = character
                                Task { await characterVM.loadDetail(character.id) }
                            }
                        }
                        .xjFadeUp(delay: 0.35)
                    }
                }
                .padding(.horizontal, -XJSpacing.md)
            }
        }
    }

    private var pinnedCharacters: [CharacterInfo] {
        characterVM.characters.filter { pinnedCharacterIDs.contains($0.id) }
    }

    private var recentCharacters: [CharacterInfo] {
        let charactersWithTime = characterVM.characters.compactMap { character -> (CharacterInfo, TimeInterval)? in
            guard let time = recentCharacterTimes[character.id] else { return nil }
            return (character, time)
        }
        return charactersWithTime
            .sorted { $0.1 > $1.1 }
            .map { $0.0 }
    }

    private var emptyPinnedSection: some View {
        VStack(spacing: XJSpacing.md) {
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.12))
                    .frame(width: 72, height: 72)
                Image(systemName: "pin")
                    .font(.system(size: 28))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 14, y: 6)

            Text(loc("还没有固定的角色"))
                .font(.headline)
                .foregroundStyle(.primary)

            Text(loc("在角色列表中右键或长按角色，选择「固定到首页」，常用角色会出现在这里"))
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)

            Button {
                appVM.selectedTab = .characters
            } label: {
                Label(loc("去角色列表"), systemImage: "person.2")
            }
            .xjPrimaryButton()
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, XJSpacing.xl)
        .xjCard()
    }

    // MARK: - 逻辑

    private func loadData() async {
        isLoadingCharacters = true
        defer { isLoadingCharacters = false }

        // 从 UserDefaults 读取固定角色 ID 集合
        let defaults = UserDefaults.standard
        if let data = defaults.data(forKey: XJDefaultsKey.pinnedCharacters),
           let ids = try? JSONDecoder().decode(Set<String>.self, from: data) {
            pinnedCharacterIDs = ids
        }

        // 读取最近对话时间
        if let times = defaults.dictionary(forKey: XJDefaultsKey.characterLastChatTime) as? [String: TimeInterval] {
            recentCharacterTimes = times
        }

        // 确保角色列表已加载
        if characterVM.characters.isEmpty {
            await characterVM.refresh()
        }
    }

    private func handleFunctionTap(_ card: FunctionCard) {
        switch card {
        case .chat:
            appVM.selectedTab = .chat
        case .characters:
            appVM.selectedTab = .characters
        case .worlds:
            appVM.selectedTab = .worlds
        case .packs:
            appVM.selectedTab = .packs
        case .memory:
            appVM.selectedTab = .memory
        case .settings:
            appVM.selectedTab = .settings
        }
    }

    private func startVoiceCall(with character: CharacterInfo) {
        let vm = VoiceCallViewModel()
        voiceCallVM = vm
        showCallSheet = true
        Task {
            await vm.startCall(characterId: character.id, characterName: character.displayName)
        }
    }
}

// MARK: - 功能卡片枚举

enum FunctionCard: Int, CaseIterable, Identifiable {
    case chat = 0
    case characters = 1
    case worlds = 2
    case packs = 3
    case memory = 4
    case settings = 5

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .chat: return loc("对话")
        case .characters: return loc("角色")
        case .worlds: return loc("世界")
        case .packs: return loc("资源包")
        case .memory: return loc("记忆")
        case .settings: return loc("设置")
        }
    }

    var subtitle: String {
        switch self {
        case .chat: return loc("与角色聊天、调参")
        case .characters: return loc("导入、管理、固定角色")
        case .worlds: return loc("世界观、NPC、剧情")
        case .packs: return loc("安装、导入资源包")
        case .memory: return loc("长短期记忆、检索")
        case .settings: return loc("外观、AI来源、偏好")
        }
    }

    var icon: String {
        switch self {
        case .chat: return "bubble.left.and.bubble.right.fill"
        case .characters: return "person.2.fill"
        case .worlds: return "globe.asia.australia.fill"
        case .packs: return "shippingbox.fill"
        case .memory: return "brain.head.profile.fill"
        case .settings: return "gearshape.fill"
        }
    }

    var accentColor: Color {
        switch self {
        case .chat: return .blue
        case .characters: return .purple
        case .worlds: return .green
        case .packs: return .orange
        case .memory: return .pink
        case .settings: return .gray
        }
    }
}

// MARK: - 功能卡片视图

struct FunctionCardView: View {
    let card: FunctionCard
    let action: () -> Void

    @Environment(ThemeSettings.self) private var theme
    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: XJSpacing.md) {
                // 图标区
                ZStack {
                    RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                        .fill(card.accentColor.opacity(0.18))
                        .frame(width: 56, height: 56)
                    Image(systemName: card.icon)
                        .font(.system(size: 24, weight: .medium))
                        .foregroundStyle(card.accentColor)
                }

                // 文字区
                VStack(alignment: .leading, spacing: 4) {
                    Text(card.title)
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text(card.subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }

                Spacer(minLength: 0)
            }
            .padding(XJSpacing.md)
            .frame(maxWidth: .infinity, minHeight: 130, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                    .fill(.regularMaterial)
            )
            .overlay(
                RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                    .strokeBorder(
                        isHovering ? card.accentColor.opacity(0.4) : Color.primary.opacity(0.06),
                        lineWidth: isHovering ? 1.5 : 1
                    )
            )
            .shadow(
                color: isHovering ? card.accentColor.opacity(0.12) : Color.black.opacity(0.04),
                radius: isHovering ? 16 : 8,
                y: isHovering ? 8 : 4
            )
            .scaleEffect(isHovering ? 1.02 : 1.0)
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            withAnimation(.spring(response: 0.35, dampingFraction: 1.0)) {
                isHovering = hovering
            }
        }
    }
}

// MARK: - 最近对话角色卡片（带悬停弹出详情和操作按钮）

struct RecentCharacterCard: View {
    let character: CharacterInfo
    let lastChatTime: TimeInterval?
    let onChat: () -> Void
    let onCall: () -> Void

    @Environment(ThemeSettings.self) private var theme
    @State private var isHovering = false
    @State private var showPopover = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Button(action: onChat) {
                VStack(alignment: .leading, spacing: XJSpacing.sm) {
                    // 头像
                    ZStack {
                        Circle()
                            .fill(theme.accentColor.opacity(0.18))
                            .frame(width: 72, height: 72)
                            .overlay(
                                Text(String(character.displayName.prefix(1)))
                                    .font(.system(size: 28, weight: .semibold))
                                    .foregroundStyle(theme.accentColor)
                            )

                        // 在线/加载状态指示器
                        if character.isLoaded {
                            Circle()
                                .fill(Color.green)
                                .frame(width: 14, height: 14)
                                .overlay(Circle().stroke(Color.white, lineWidth: 2))
                                .offset(x: 26, y: 26)
                        }
                    }

                    VStack(alignment: .leading, spacing: 3) {
                        Text(character.displayName)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.primary)
                            .lineLimit(1)

                        HStack(spacing: 6) {
                            if character.isLoaded {
                                Label(loc("已加载"), systemImage: "checkmark.circle.fill")
                                    .font(.caption2)
                                    .foregroundStyle(.green)
                            }
                            if character.isFromPack {
                                Label(loc("包"), systemImage: "shippingbox.fill")
                                    .font(.caption2)
                                    .foregroundStyle(.purple)
                            }
                        }

                        if let lastChatTime {
                            Text(formatRelativeTime(lastChatTime))
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }

                    Spacer(minLength: 0)
                }
                .padding(XJSpacing.md)
                .frame(width: 160, height: 160, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                        .fill(.regularMaterial)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                        .strokeBorder(
                            isHovering ? theme.accentColor.opacity(0.4) : Color.primary.opacity(0.06),
                            lineWidth: isHovering ? 1.5 : 1
                        )
                )
                .shadow(
                    color: isHovering ? theme.accentColor.opacity(0.12) : Color.black.opacity(0.04),
                    radius: isHovering ? 16 : 8,
                    y: isHovering ? 8 : 4
                )
                .scaleEffect(isHovering ? 1.03 : 1.0)
            }
            .buttonStyle(.plain)
            .onHover { hovering in
                withAnimation(.spring(response: 0.35, dampingFraction: 1.0)) {
                    isHovering = hovering
                    showPopover = hovering
                }
            }

            // 悬停弹出层
            if showPopover {
                RecentCharacterPopover(
                    character: character,
                    lastChatTime: lastChatTime,
                    onChat: {
                        showPopover = false
                        onChat()
                    },
                    onCall: {
                        showPopover = false
                        onCall()
                    },
                    onDismiss: {
                        showPopover = false
                    }
                )
                .offset(x: 8, y: -8)
                .transition(.opacity.combined(with: .scale(scale: 0.95)))
                .zIndex(1)
            }
        }
        .frame(width: 160, height: 160)
    }

    private func formatRelativeTime(_ timestamp: TimeInterval) -> String {
        let date = Date(timeIntervalSince1970: timestamp)
        let interval = Date().timeIntervalSince(date)

        if interval < 60 {
            return loc("刚刚")
        } else if interval < 3600 {
            let minutes = Int(interval / 60)
            return loc("%lld 分钟前", minutes)
        } else if interval < 86400 {
            let hours = Int(interval / 3600)
            return loc("%lld 小时前", hours)
        } else if interval < 604800 {
            let days = Int(interval / 86400)
            return loc("%lld 天前", days)
        } else {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "zh_CN")
            formatter.dateFormat = "MM-dd"
            return formatter.string(from: date)
        }
    }
}

/// 最近对话角色悬停弹出层
struct RecentCharacterPopover: View {
    let character: CharacterInfo
    let lastChatTime: TimeInterval?
    let onChat: () -> Void
    let onCall: () -> Void
    let onDismiss: () -> Void

    @Environment(ThemeSettings.self) private var theme

    var body: some View {
        VStack(alignment: .leading, spacing: XJSpacing.sm) {
            // 头像 + 名字
            HStack(spacing: 10) {
                Circle()
                    .fill(theme.accentColor.opacity(0.18))
                    .frame(width: 48, height: 48)
                    .overlay(
                        Text(String(character.displayName.prefix(1)))
                            .font(.title2.weight(.semibold))
                            .foregroundStyle(theme.accentColor)
                    )

                VStack(alignment: .leading, spacing: 2) {
                    Text(character.displayName)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.primary)
                        .lineLimit(1)

                    if let lastChatTime {
                        Text(loc("最近对话：%@", formatRelativeTime(lastChatTime)))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            // 描述
            if let personaDoc = character.persona_doc, !personaDoc.isEmpty {
                Text(personaDoc)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .frame(maxWidth: 220, alignment: .leading)
            } else {
                Text(loc("暂无人设描述"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: 220, alignment: .leading)
            }

            Divider()

            // 操作按钮
            HStack(spacing: 8) {
                Button(action: onChat) {
                    Label(loc("聊天"), systemImage: "bubble.left.and.bubble.right")
                        .font(.caption.weight(.medium))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)

                Button(action: onCall) {
                    Label(loc("通话"), systemImage: "phone")
                        .font(.caption.weight(.medium))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(XJSpacing.md)
        .frame(width: 260)
        .background(
            RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                .fill(.regularMaterial)
                .shadow(color: Color.black.opacity(0.15), radius: 20, y: 10)
        )
        .overlay(
            RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
        )
        .onHover { hovering in
            if !hovering {
                // 延迟一点再关闭，防止鼠标在卡片和弹出层之间移动时闪烁
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    onDismiss()
                }
            }
        }
    }

    private func formatRelativeTime(_ timestamp: TimeInterval) -> String {
        let date = Date(timeIntervalSince1970: timestamp)
        let interval = Date().timeIntervalSince(date)

        if interval < 60 {
            return loc("刚刚")
        } else if interval < 3600 {
            let minutes = Int(interval / 60)
            return loc("%lld 分钟前", minutes)
        } else if interval < 86400 {
            let hours = Int(interval / 3600)
            return loc("%lld 小时前", hours)
        } else if interval < 604800 {
            let days = Int(interval / 86400)
            return loc("%lld 天前", days)
        } else {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "zh_CN")
            formatter.dateFormat = "MM-dd"
            return formatter.string(from: date)
        }
    }
}

// MARK: - 固定角色卡片

struct PinnedCharacterCard: View {
    let character: CharacterInfo
    let action: () -> Void

    @Environment(ThemeSettings.self) private var theme
    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: XJSpacing.sm) {
                // 头像 + 固定徽标
                ZStack(alignment: .topTrailing) {
                    Circle()
                        .fill(theme.accentColor.opacity(0.18))
                        .frame(width: 72, height: 72)
                        .overlay(
                            Text(String(character.displayName.prefix(1)))
                                .font(.system(size: 28, weight: .semibold))
                                .foregroundStyle(theme.accentColor)
                        )

                    // 固定徽标
                    Image(systemName: "pin.fill")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(4)
                        .background(Circle().fill(Color.orange))
                        .offset(x: 8, y: -8)

                    // 在线/加载状态指示器
                    if character.isLoaded {
                        Circle()
                            .fill(Color.green)
                            .frame(width: 14, height: 14)
                            .overlay(Circle().stroke(Color.white, lineWidth: 2))
                            .offset(x: 26, y: 26)
                    }
                }

                VStack(alignment: .leading, spacing: 3) {
                    Text(character.displayName)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.primary)
                        .lineLimit(1)

                    HStack(spacing: 6) {
                        if character.isLoaded {
                            Label(loc("已加载"), systemImage: "checkmark.circle.fill")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
                        if character.isFromPack {
                            Label(loc("包"), systemImage: "shippingbox.fill")
                                .font(.caption2)
                                .foregroundStyle(.purple)
                        }
                    }
                }

                Spacer(minLength: 0)
            }
            .padding(XJSpacing.md)
            .frame(width: 160, height: 160, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                    .fill(.regularMaterial)
            )
            .overlay(
                RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                    .strokeBorder(
                        isHovering ? theme.accentColor.opacity(0.4) : Color.primary.opacity(0.06),
                        lineWidth: isHovering ? 1.5 : 1
                    )
            )
            .shadow(
                color: isHovering ? theme.accentColor.opacity(0.12) : Color.black.opacity(0.04),
                radius: isHovering ? 16 : 8,
                y: isHovering ? 8 : 4
            )
            .scaleEffect(isHovering ? 1.03 : 1.0)
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            withAnimation(.spring(response: 0.35, dampingFraction: 1.0)) {
                isHovering = hovering
            }
        }
        .contextMenu {
            Button {
                togglePin()
            } label: {
                Label(pinnedCharacterIDs.contains(character.id) ? loc("取消固定") : loc("固定到首页"),
                      systemImage: pinnedCharacterIDs.contains(character.id) ? "pin.slash" : "pin")
            }

            Divider()

            Button {
                action()
            } label: {
                Label(loc("查看详情"), systemImage: "info.circle")
            }
        }
    }

    @AppStorage(XJDefaultsKey.pinnedCharacters) private var pinnedCharactersData: Data = Data()
    private var pinnedCharacterIDs: Set<String> {
        get {
            (try? JSONDecoder().decode(Set<String>.self, from: pinnedCharactersData)) ?? []
        }
        set {
            pinnedCharactersData = (try? JSONEncoder().encode(newValue)) ?? Data()
        }
    }

    private func togglePin() {
        var ids = pinnedCharacterIDs
        if ids.contains(character.id) {
            ids.remove(character.id)
        } else {
            ids.insert(character.id)
        }
        pinnedCharactersData = (try? JSONEncoder().encode(ids)) ?? Data()
    }
}