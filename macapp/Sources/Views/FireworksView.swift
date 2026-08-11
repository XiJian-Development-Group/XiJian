import SwiftUI

/// 烟花粒子动效 — 随机火箭升空 → 爆炸 → 火花重力下落 + 拖尾发光。
/// 尊重「减弱动态效果」：开启时静态显示（不启动粒子、不更新画布）。
/// 物理与爆裂逻辑全部收敛在 `FireworksEngine` 静态纯函数中，便于单元测试。
struct FireworksView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var systemColorScheme
    @State private var particles: [Particle] = []
    @State private var lastUpdate: Date = .now
    @State private var nextLaunchAt: Date = .now

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60, paused: reduceMotion)) { timeline in
            GeometryReader { proxy in
                Canvas { context, size in
                    context.blendMode = .plusLighter
                    for p in particles {
                        let alpha = 1 - p.age / p.maxAge
                        var path = Path()
                        path.move(to: p.previous)
                        path.addLine(to: p.position)
                        context.stroke(path, with: .color(p.color.opacity(alpha)), lineWidth: p.size)
                        context.fill(
                            Path(ellipseIn: CGRect(x: p.position.x - p.size / 2,
                                                   y: p.position.y - p.size / 2,
                                                   width: p.size, height: p.size)),
                            with: .color(p.color.opacity(alpha))
                        )
                    }
                }
                .onChange(of: timeline.date) { _, now in
                    guard !reduceMotion else { return }
                    // 帧间隔封顶 1/20s，避免长时间挂起后恢复时粒子瞬移
                    let dt = min(now.timeIntervalSince(lastUpdate), 1.0 / 20)
                    lastUpdate = now
                    step(dt: dt, size: proxy.size)
                }
            }
        }
        .onAppear {
            lastUpdate = .now
            nextLaunchAt = .now
            if reduceMotion { particles = [] }
        }
        .onChange(of: reduceMotion) { _, newValue in
            if newValue { particles = [] }
        }
        .drawingGroup()
        // U12：遮罩色跟随主题（浅色下用浅色遮罩，避免深色块突兀）
        .background(
            (systemColorScheme == .light ? Color.white : Color.black).opacity(0.35)
        )
    }

    // MARK: - 帧推进

    /// 每帧推进：定时发射火箭 → 物理步进 → 火箭到顶爆裂 → 数量封顶
    private func step(dt: Double, size: CGSize) {
        guard size.width > 0, size.height > 0 else { return }

        // 定时发射火箭（随机间隔 0.5...1.2 秒）
        if Date.now >= nextLaunchAt {
            particles.append(FireworksEngine.launchRocket(size: size))
            nextLaunchAt = Date.now.addingTimeInterval(.random(in: 0.5...1.2))
        }

        // 物理推进（重力 / 阻力 / 老化 / 淘汰）
        FireworksEngine.step(&particles, dt: dt, size: size)

        // 火箭到达爆裂高度 → 生成火花
        var exploded: [Particle] = []
        var survivors: [Particle] = []
        for p in particles {
            if p.isRocket, p.position.y <= p.explodeY {
                exploded.append(contentsOf: FireworksEngine.burst(at: p.position))
            } else {
                survivors.append(p)
            }
        }
        particles = survivors + exploded

        // 粒子上限，超出丢弃最早生成的
        if particles.count > FireworksEngine.maxParticles {
            particles.removeFirst(particles.count - FireworksEngine.maxParticles)
        }
    }
}

// MARK: - 粒子

/// 单个粒子：火箭或爆裂火花。`previous` 用于画拖尾线段。
struct Particle: Identifiable {
    let id = UUID()
    /// 当前位置
    var position: CGPoint
    /// 速度（pt/s；火箭向上为负 dy）
    var velocity: CGVector
    /// 颜色
    var color: Color
    /// 尺寸（线宽 / 圆点直径）
    var size: CGFloat
    /// 已存活时间（秒）
    var age: Double = 0
    /// 最大寿命（秒），超过即死亡
    var maxAge: Double
    /// 上一帧位置（拖尾线段起点）
    var previous: CGPoint
    /// 是否为火箭（火箭不受重力，到达 explodeY 后爆裂）
    var isRocket: Bool = false
    /// 火箭爆裂高度（y 坐标阈值）
    var explodeY: CGFloat = 0

    /// 是否已死亡（超龄）
    var isDead: Bool { age >= maxAge }
}

// MARK: - 烟花引擎（纯函数，可单测）

/// 烟花物理与爆裂生成逻辑（纯函数，无视图依赖）。
/// 主代理负责为这些函数补充单元测试。
enum FireworksEngine {
    /// 粒子上限（超出丢弃最早的）
    static let maxParticles = 600
    /// 预设调色板（橙 / 金 / 粉 / 青 / 紫 / 白）
    static let palette: [Color] = [.orange, .yellow, .pink, .cyan, .purple, .white]
    /// 重力加速度（pt/s²，火花下落用）
    static let gravity: CGFloat = 600

    /// 生成一枚火箭粒子：随机横向位置（宽度 10%...90%），从底部升空，
    /// 初始上升速度 500...700 pt/s，爆裂高度为窗口高度的 35%...75%。
    static func launchRocket(size: CGSize) -> Particle {
        let x = size.width * CGFloat.random(in: 0.1...0.9)
        let targetY = size.height * CGFloat.random(in: 0.35...0.75)
        return Particle(
            position: CGPoint(x: x, y: size.height + 20),
            velocity: CGVector(dx: 0, dy: -CGFloat.random(in: 500...700)),
            color: .white.opacity(0.9),
            size: 2.5,
            maxAge: 4,
            previous: CGPoint(x: x, y: size.height + 20),
            isRocket: true,
            explodeY: targetY
        )
    }

    /// 在指定位置爆裂：生成 40...80 个火花，随机方向（0..2π）、
    /// 速度 60...320 pt/s、随机调色板颜色、寿命 1.0...2.2 秒。
    static func burst(at point: CGPoint) -> [Particle] {
        let count = Int.random(in: 40...80)
        let color = palette.randomElement() ?? .white
        return (0..<count).map { _ in
            let angle = CGFloat.random(in: 0...(2 * .pi))
            let speed = CGFloat.random(in: 60...320)
            return Particle(
                position: point,
                velocity: CGVector(dx: cos(angle) * speed, dy: sin(angle) * speed),
                color: color.opacity(CGFloat.random(in: 0.7...1)),
                size: CGFloat.random(in: 1.5...3.5),
                maxAge: Double.random(in: 1.0...2.2),
                previous: point,
                isRocket: false
            )
        }
    }

    /// 物理步进：火花受重力 + 空气阻力，火箭仅轻微横向阻尼；
    /// 位置按 dt 积分，年龄累加，淘汰死亡粒子并封顶数量。
    static func step(_ particles: inout [Particle], dt: Double, size: CGSize) {
        guard size.width > 0, size.height > 0 else { return }
        let dtCGFloat = CGFloat(dt)
        for i in particles.indices {
            particles[i].age += dt
            let p = particles[i]
            if p.isRocket {
                // 火箭：上升中无重力，仅轻微横向阻力
                let drag = CGFloat(pow(0.995, dt * 60))
                particles[i].velocity.dx *= drag
            } else {
                // 火花：重力 + 指数阻尼
                particles[i].velocity.dy += gravity * dtCGFloat
                let drag = CGFloat(pow(0.999, dt * 60))
                particles[i].velocity.dx *= drag
                particles[i].velocity.dy *= drag
            }
            particles[i].previous = p.position
            particles[i].position.x += p.velocity.dx * dtCGFloat
            particles[i].position.y += p.velocity.dy * dtCGFloat
        }
        particles.removeAll { $0.isDead }
        if particles.count > maxParticles {
            particles.removeFirst(particles.count - maxParticles)
        }
    }
}
