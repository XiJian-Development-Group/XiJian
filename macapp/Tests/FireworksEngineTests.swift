import XCTest
import SwiftUI
import AppKit
@testable import XiJianKit

/// 烟花引擎纯函数测试（FireworksEngine：物理 / 爆裂 / 火箭生成 / 淘汰）
final class FireworksEngineTests: XCTestCase {

    private let size = CGSize(width: 800, height: 600)

    // MARK: - 火箭生成

    func testLaunchRocketStartsAtBottomAndMovesUp() {
        let rocket = FireworksEngine.launchRocket(size: size)
        XCTAssertTrue(rocket.isRocket, "火箭应标记 isRocket")
        XCTAssertGreaterThan(rocket.position.y, size.height - 40, "火箭应从底部附近升空")
        XCTAssertLessThan(rocket.velocity.dy, 0, "火箭初始速度应向上（dy < 0）")
        XCTAssertEqual(rocket.position.x, rocket.previous.x, "火箭初始 previous 应与 position 相同")
    }

    func testLaunchRocketWithinHorizontalRange() {
        for _ in 0..<50 {
            let rocket = FireworksEngine.launchRocket(size: size)
            XCTAssertGreaterThanOrEqual(rocket.position.x, size.width * 0.1)
            XCTAssertLessThanOrEqual(rocket.position.x, size.width * 0.9)
        }
    }

    // MARK: - 爆裂

    func testBurstGeneratesParticlesWithinRange() {
        for _ in 0..<20 {
            let particles = FireworksEngine.burst(at: CGPoint(x: 400, y: 300))
            XCTAssertGreaterThanOrEqual(particles.count, 40, "爆裂火花数量应 >= 40")
            XCTAssertLessThanOrEqual(particles.count, 80, "爆裂火花数量应 <= 80")
            for p in particles {
                XCTAssertFalse(p.isRocket, "爆裂火花不应是火箭")
                XCTAssertEqual(p.position, CGPoint(x: 400, y: 300), "火花初始位置应在爆裂点")
                XCTAssertGreaterThan(p.maxAge, 0, "火花应有正寿命")
            }
        }
    }

    func testBurstUsesPaletteColor() {
        let palette = FireworksEngine.palette
        XCTAssertFalse(palette.isEmpty, "调色板不应为空")
        let particles = FireworksEngine.burst(at: .zero)
        for p in particles {
            // Color == 对系统色转换不稳定，改用 sRGB 分量近似比较（容差 0.03）
            let ns = NSColor(p.color).usingColorSpace(.sRGB) ?? NSColor(p.color)
            let matched = palette.contains { candidate in
                let cns = NSColor(candidate).usingColorSpace(.sRGB) ?? NSColor(candidate)
                return abs(ns.redComponent - cns.redComponent) < 0.03
                    && abs(ns.greenComponent - cns.greenComponent) < 0.03
                    && abs(ns.blueComponent - cns.blueComponent) < 0.03
            }
            XCTAssertTrue(matched, "火花颜色应来自调色板")
        }
    }

    // MARK: - 物理步进

    func testStepAppliesGravityToSparks() {
        var particles = FireworksEngine.burst(at: CGPoint(x: 400, y: 300))
        let before = particles.map(\.velocity.dy)
        FireworksEngine.step(&particles, dt: 1.0 / 60, size: size)
        for i in particles.indices {
            XCTAssertGreaterThan(particles[i].velocity.dy, before[i], "火花应受重力（dy 增大）")
        }
    }

    func testStepDoesNotApplyGravityToRockets() {
        var particles = [FireworksEngine.launchRocket(size: size)]
        let beforeDY = particles[0].velocity.dy
        FireworksEngine.step(&particles, dt: 1.0 / 60, size: size)
        XCTAssertEqual(particles[0].velocity.dy, beforeDY, accuracy: 1.0, "火箭不应受重力")
    }

    func testStepMovesParticlesByVelocity() {
        var particles = FireworksEngine.burst(at: CGPoint(x: 400, y: 300))
        let before = particles.map(\.position)
        FireworksEngine.step(&particles, dt: 1.0 / 60, size: size)
        for i in particles.indices {
            XCTAssertNotEqual(particles[i].position, before[i], "粒子位置应按速度积分移动")
        }
    }

    func testStepRemovesDeadParticles() {
        var particles = FireworksEngine.burst(at: .zero)
        // 把所有粒子寿命推到超龄
        for i in particles.indices {
            particles[i].age = particles[i].maxAge + 1
        }
        FireworksEngine.step(&particles, dt: 1.0 / 60, size: size)
        XCTAssertTrue(particles.isEmpty, "超龄粒子应被淘汰")
    }

    func testStepCapsParticleCount() {
        var particles = (0..<(FireworksEngine.maxParticles + 200)).map { i in
            Particle(
                position: .zero,
                velocity: .zero,
                color: .white,
                size: 2,
                maxAge: 10,
                previous: .zero
            )
        }
        FireworksEngine.step(&particles, dt: 1.0 / 60, size: size)
        XCTAssertLessThanOrEqual(particles.count, FireworksEngine.maxParticles, "粒子数应被封顶")
    }

    func testStepGuardsZeroSize() {
        var particles = FireworksEngine.burst(at: .zero)
        let before = particles.count
        FireworksEngine.step(&particles, dt: 1.0 / 60, size: .zero)
        XCTAssertEqual(particles.count, before, "零尺寸画布不应推进物理")
    }
}

// MARK: - Color 辅助（调色板比较）

private extension Color {
    /// 去掉透明度后用于比较（调色板内颜色均为不透明，opacity 应用在粒子层）
    var withoutOpacity: Color {
        let ns = NSColor(self).usingColorSpace(.sRGB) ?? NSColor(self)
        return Color(
            red: ns.redComponent,
            green: ns.greenComponent,
            blue: ns.blueComponent
        )
    }
}
