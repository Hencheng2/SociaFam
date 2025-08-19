// Ensure the DOM is fully loaded before trying to access elements
document.addEventListener('DOMContentLoaded', () => {

    // --- 1. Basic Scene Setup ---
    let scene, camera, renderer;
    let cube; // Our simple test object

    function init() {
        // Get the canvas element from our HTML
        const canvas = document.getElementById('gameCanvas');

        // Create a new Three.js Scene
        scene = new THREE.Scene();

        // Set a background color for the scene (optional, but good for visibility)
        scene.background = new THREE.Color(0x333333); // Dark grey

        // Create a Perspective Camera
        // Parameters: Field of View (FOV), Aspect Ratio, Near Clipping Plane, Far Clipping Plane
        // FOV: How "wide" the view is (in degrees)
        // Aspect Ratio: Width of element / Height of element (important for correct perspective)
        // Near/Far: Objects closer than 'near' or further than 'far' won't be rendered
        camera = new THREE.PerspectiveCamera(
            75,
            window.innerWidth / window.innerHeight, // Initial aspect ratio (will be updated on resize)
            0.1,
            1000
        );

        // Position the camera
        camera.position.z = 5; // Move camera 5 units back from the origin

        // Create a WebGL Renderer
        renderer = new THREE.WebGLRenderer({
            canvas: canvas, // Tell the renderer to use our specific canvas
            antialias: true // Makes edges smoother
        });

        // Set the size of the renderer (same as the canvas)
        renderer.setSize(window.innerWidth, window.innerHeight);

        // Append the renderer's DOM element to the body (not strictly needed if using 'canvas: canvas')
        // document.body.appendChild(renderer.domElement);

        // --- 2. Add Objects to the Scene ---

        // Create a Box Geometry (shape)
        const geometry = new THREE.BoxGeometry(1, 1, 1); // 1 unit wide, high, deep

        // Create a Material (how the object looks - color, texture, etc.)
        // MeshBasicMaterial is simple, not affected by lights
        const material = new THREE.MeshBasicMaterial({ color: 0x00ff00 }); // Green color

        // Create a Mesh (combination of geometry and material)
        cube = new THREE.Mesh(geometry, material);

        // Add the cube to the scene
        scene.add(cube);

        // --- 3. Lighting (Crucial for non-basic materials) ---
        // Although MeshBasicMaterial doesn't need lights, it's good practice to add them now.
        // If you switch to MeshStandardMaterial, you'll need lights to see anything.

        // Ambient Light: Illuminates all objects in the scene equally from all directions.
        // No shadows, no directionality. Good for general brightness.
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.5); // White light, 50% intensity
        scene.add(ambientLight);

        // Directional Light: Simulates light from a distant source (like the sun).
        // Has a direction and can cast shadows.
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8); // White light, 80% intensity
        directionalLight.position.set(1, 1, 1).normalize(); // Position it in front, top, right
        scene.add(directionalLight);

        // --- 4. Handle Window Resizing ---
        // Make the renderer and camera adjust if the browser window changes size
        window.addEventListener('resize', onWindowResize, false);
    }

    function onWindowResize() {
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix(); // Update the camera's projection matrix
        renderer.setSize(window.innerWidth, window.innerHeight);
    }


    // --- 5. Animation Loop ---
    function animate() {
        requestAnimationFrame(animate); // Request the next frame

        // Rotate the cube
        cube.rotation.x += 0.01;
        cube.rotation.y += 0.01;

        renderer.render(scene, camera); // Render the scene
    }

    // Initialize the scene and start the animation
    init();
    animate();
});
