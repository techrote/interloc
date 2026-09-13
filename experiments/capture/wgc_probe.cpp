#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <windows.graphics.capture.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>

#include <winrt/base.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "windowsapp.lib")
#pragma comment(lib, "user32.lib")

using winrt::Windows::Graphics::Capture::Direct3D11CaptureFrame;
using winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool;
using winrt::Windows::Graphics::Capture::GraphicsCaptureItem;
using winrt::Windows::Graphics::Capture::GraphicsCaptureSession;
using winrt::Windows::Graphics::DirectX::DirectXPixelFormat;
using winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;

namespace {

struct TargetIdentity {
    HWND hwnd{};
    DWORD pid{};
};

TargetIdentity validate_target(HWND hwnd, DWORD expected_pid) {
    if (!::IsWindow(hwnd)) {
        throw std::runtime_error("target HWND is not valid");
    }
    DWORD pid = 0;
    ::GetWindowThreadProcessId(hwnd, &pid);
    if (pid == 0 || pid != expected_pid) {
        throw std::runtime_error("target PID does not match expected PID");
    }
    return {hwnd, pid};
}

IDirect3DDevice make_device(winrt::com_ptr<ID3D11Device>& native_device,
                            winrt::com_ptr<ID3D11DeviceContext>& native_context) {
    UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    D3D_FEATURE_LEVEL feature_level{};
    winrt::check_hresult(::D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        flags,
        nullptr,
        0,
        D3D11_SDK_VERSION,
        native_device.put(),
        &feature_level,
        native_context.put()));

    auto dxgi_device = native_device.as<IDXGIDevice>();
    winrt::com_ptr<IInspectable> inspectable;
    winrt::check_hresult(::CreateDirect3D11DeviceFromDXGIDevice(dxgi_device.get(), inspectable.put()));
    return inspectable.as<IDirect3DDevice>();
}

GraphicsCaptureItem make_item(HWND hwnd) {
    auto activation_factory = winrt::get_activation_factory<GraphicsCaptureItem>();
    auto interop = activation_factory.as<IGraphicsCaptureItemInterop>();
    winrt::com_ptr<ABI::Windows::Graphics::Capture::IGraphicsCaptureItem> abi_item;
    winrt::check_hresult(interop->CreateForWindow(
        hwnd,
        winrt::guid_of<ABI::Windows::Graphics::Capture::IGraphicsCaptureItem>(),
        abi_item.put_void()));
    GraphicsCaptureItem item{nullptr};
    winrt::copy_from_abi(item, abi_item.get());
    return item;
}

void write_bmp(const std::filesystem::path& output,
               ID3D11Device* device,
               ID3D11DeviceContext* context,
               ID3D11Texture2D* source) {
    D3D11_TEXTURE2D_DESC desc{};
    source->GetDesc(&desc);
    if (desc.Width == 0 || desc.Height == 0 || desc.Width > 4096 || desc.Height > 4096) {
        throw std::runtime_error("captured dimensions are outside IL-012 bounds");
    }
    if (desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM) {
        throw std::runtime_error("unexpected capture pixel format");
    }

    D3D11_TEXTURE2D_DESC staging_desc = desc;
    staging_desc.BindFlags = 0;
    staging_desc.MiscFlags = 0;
    staging_desc.Usage = D3D11_USAGE_STAGING;
    staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

    winrt::com_ptr<ID3D11Texture2D> staging;
    winrt::check_hresult(device->CreateTexture2D(&staging_desc, nullptr, staging.put()));
    context->CopyResource(staging.get(), source);

    D3D11_MAPPED_SUBRESOURCE mapped{};
    winrt::check_hresult(context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
    struct UnmapGuard {
        ID3D11DeviceContext* context;
        ID3D11Texture2D* texture;
        ~UnmapGuard() { context->Unmap(texture, 0); }
    } unmap{context, staging.get()};

    const std::uint32_t row_bytes = desc.Width * 4u;
    const std::uint32_t pixel_bytes = row_bytes * desc.Height;

    BITMAPFILEHEADER file_header{};
    file_header.bfType = 0x4D42;
    file_header.bfOffBits = sizeof(BITMAPFILEHEADER) + sizeof(BITMAPINFOHEADER);
    file_header.bfSize = file_header.bfOffBits + pixel_bytes;

    BITMAPINFOHEADER info{};
    info.biSize = sizeof(BITMAPINFOHEADER);
    info.biWidth = static_cast<LONG>(desc.Width);
    info.biHeight = -static_cast<LONG>(desc.Height); // top-down
    info.biPlanes = 1;
    info.biBitCount = 32;
    info.biCompression = BI_RGB;
    info.biSizeImage = pixel_bytes;

    std::ofstream stream(output, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error("cannot open output BMP");
    }
    stream.write(reinterpret_cast<const char*>(&file_header), sizeof(file_header));
    stream.write(reinterpret_cast<const char*>(&info), sizeof(info));
    const auto* base = static_cast<const std::uint8_t*>(mapped.pData);
    for (UINT row = 0; row < desc.Height; ++row) {
        stream.write(reinterpret_cast<const char*>(base + row * mapped.RowPitch), row_bytes);
    }
    if (!stream) {
        throw std::runtime_error("failed writing output BMP");
    }
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    // Usage: wgc_probe.exe <hwnd> <expected-pid> <output.bmp> [timeout-ms]
    if (argc < 4 || argc > 5) {
        std::wcerr << L"usage: wgc_probe.exe <hwnd> <expected-pid> <output.bmp> [timeout-ms]\n";
        return 64;
    }

    try {
        winrt::init_apartment(winrt::apartment_type::multi_threaded);
        if (!GraphicsCaptureSession::IsSupported()) {
            throw std::runtime_error("Windows Graphics Capture is not supported on this host");
        }

        const auto hwnd_value = std::stoull(argv[1], nullptr, 0);
        const HWND hwnd = reinterpret_cast<HWND>(static_cast<std::uintptr_t>(hwnd_value));
        const DWORD expected_pid = static_cast<DWORD>(std::stoul(argv[2], nullptr, 0));
        const std::filesystem::path output(argv[3]);
        const auto timeout_ms = argc == 5 ? std::stoul(argv[4], nullptr, 0) : 10000UL;
        if (timeout_ms == 0 || timeout_ms > 10000UL) {
            throw std::runtime_error("timeout must be in 1..10000 ms");
        }

        validate_target(hwnd, expected_pid);
        auto item = make_item(hwnd);

        winrt::com_ptr<ID3D11Device> native_device;
        winrt::com_ptr<ID3D11DeviceContext> native_context;
        auto device = make_device(native_device, native_context);
        auto size = item.Size();
        if (size.Width <= 0 || size.Height <= 0 || size.Width > 4096 || size.Height > 4096) {
            throw std::runtime_error("target dimensions are outside IL-012 bounds");
        }

        auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            1,
            size);
        auto session = pool.CreateCaptureSession(item);

        std::mutex mutex;
        std::condition_variable condition;
        Direct3D11CaptureFrame captured{nullptr};
        auto token = pool.FrameArrived([&](Direct3D11CaptureFramePool const& sender, winrt::Windows::Foundation::IInspectable const&) {
            try {
                auto frame = sender.TryGetNextFrame();
                std::lock_guard<std::mutex> lock(mutex);
                if (captured == nullptr) {
                    captured = frame;
                    condition.notify_one();
                }
            } catch (...) {
                condition.notify_one();
            }
        });

        const auto started = std::chrono::steady_clock::now();
        session.StartCapture();
        {
            std::unique_lock<std::mutex> lock(mutex);
            if (!condition.wait_for(lock, std::chrono::milliseconds(timeout_ms), [&] { return captured != nullptr; })) {
                pool.FrameArrived(token);
                session.Close();
                pool.Close();
                throw std::runtime_error("timed out waiting for first capture frame");
            }
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - started).count();

        validate_target(hwnd, expected_pid);
        auto access = captured.Surface().as<IDirect3DDxgiInterfaceAccess>();
        winrt::com_ptr<ID3D11Texture2D> texture;
        winrt::check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D), texture.put_void()));
        write_bmp(output, native_device.get(), native_context.get(), texture.get());

        pool.FrameArrived(token);
        session.Close();
        pool.Close();

        D3D11_TEXTURE2D_DESC desc{};
        texture->GetDesc(&desc);
        std::wcout << L"{\"backend\":\"wgc\",\"width\":" << desc.Width
                   << L",\"height\":" << desc.Height
                   << L",\"capture_ms\":" << elapsed
                   << L",\"pid\":" << expected_pid << L"}\n";
        return 0;
    } catch (winrt::hresult_error const& exc) {
        std::wcerr << L"WGC HRESULT failure: " << exc.message().c_str() << L"\n";
        return 2;
    } catch (std::exception const& exc) {
        std::cerr << "WGC failure: " << exc.what() << "\n";
        return 2;
    }
}
