import Foundation

@Observable
final class CharacterViewModel {
    var characters: [ServerCharacter] = []
    var selectedCharacter: ServerCharacter?
    var isLoading: Bool = false
    var errorMessage: String?

    private let apiClient: APIClient

    init(apiClient: APIClient) {
        self.apiClient = apiClient
    }

    func fetchCharacters(baseURL: String) async {
        isLoading = true
        errorMessage = nil

        do {
            let rawCharacters = try await apiClient.listCharacters(baseURL: baseURL)
            characters = rawCharacters.compactMap { dict in
                guard let id = dict["id"] as? String ?? dict["_id"] as? String,
                      let name = dict["name"] as? String else { return nil }
                let persona = dict["persona"] as? String ?? ""
                return ServerCharacter(id: id, name: name, persona: persona)
            }
        } catch {
            errorMessage = "获取角色列表失败: \(error.localizedDescription)"
        }

        isLoading = false
    }

    func createCharacter(baseURL: String, name: String, persona: String) async {
        errorMessage = nil

        do {
            let result = try await apiClient.createCharacter(baseURL: baseURL, name: name, persona: persona)
            if let id = result["id"] as? String {
                let char = ServerCharacter(id: id, name: name, persona: persona)
                characters.append(char)
                selectedCharacter = char
            }
        } catch {
            errorMessage = "创建角色失败: \(error.localizedDescription)"
        }
    }

    func updateCharacter(baseURL: String, id: String, name: String, persona: String) async {
        errorMessage = nil

        do {
            _ = try await apiClient.updateCharacter(baseURL: baseURL, id: id, name: name, persona: persona)
            if let idx = characters.firstIndex(where: { $0.id == id }) {
                characters[idx].name = name
                characters[idx].persona = persona
            }
        } catch {
            errorMessage = "更新角色失败: \(error.localizedDescription)"
        }
    }

    func deleteCharacter(at id: String) {
        characters.removeAll { $0.id == id }
        if selectedCharacter?.id == id {
            selectedCharacter = characters.first
        }
    }
}
