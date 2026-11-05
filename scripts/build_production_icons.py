import subprocess
from PIL import Image

def build_all():
    # 1. Render master 1024x1024 PNG from frontend/public/favicon.svg using sips
    svg_path = "frontend/public/favicon.svg"
    master_png = "frontend/public/app-icon.png"
    
    subprocess.run([
        "sips", "-s", "format", "png",
        "-z", "1024", "1024",
        svg_path, "--out", master_png
    ], check=True)
    print("Generated frontend/public/app-icon.png (1024x1024)")
    
    # 2. Resize to all required sizes using PIL Lanczos for maximum quality
    master = Image.open(master_png)
    
    sizes = {
        "icon-512.png": 512,
        "icon-192.png": 192,
        "apple-touch-icon.png": 180,
        "favicon-32x32.png": 32,
        "favicon-16x16.png": 16,
    }
    
    for filename, s in sizes.items():
        res = master.resize((s, s), Image.Resampling.LANCZOS)
        res.save(f"frontend/public/{filename}", format="PNG")
        print(f"Generated frontend/public/{filename} ({s}x{s})")
        
    # 3. Multi-resolution ICO (16, 32, 48)
    ico_sizes = [(16, 16), (32, 32), (48, 48)]
    ico_images = [master.resize(sz, Image.Resampling.LANCZOS) for sz in ico_sizes]
    ico_images[0].save(
        "frontend/public/favicon.ico",
        format="ICO",
        sizes=ico_sizes,
        append_images=ico_images[1:]
    )
    print("Generated frontend/public/favicon.ico")

if __name__ == "__main__":
    build_all()
