"""
Functions for rendering a single MANO model to image and manifold
"""
import moderngl

from PIL import Image, ImageDraw, ImageFont
from matplotlib import pyplot as plt

from utils.mano_utils import *

vertex_shader = '''
               #version 330 core
               in vec3 in_vert;
                //out vec3 out_normal;

               void main() {
                   gl_Position = vec4(in_vert, 1.0);
               }
               '''
fragment_shader = '''
                #version 330 core
                
                uniform vec3 color;
                
                in vec3 out_normal;
                out vec4 f_color;
                
                const vec3 light1 = vec3(1, 1, -1);
                const vec3 light2 = vec3(0, 0, 1);
                
                const vec3 ambientLight = vec3(0.13, 0.13, 0.13);
                const float shininess = 16.0;
                void main() {
                    vec3 viewDir = vec3(0,0,1);
                    vec3 normal = normalize(out_normal);
                    float lambert1 = max(0, dot(normal, -normalize(light1)));
                    float lambert2 = max(0, dot(normal, -normalize(light2)));
                
                    vec3 halfDir = normalize(light2 + viewDir);
                    float specAngle = max(dot(halfDir, normal), 0.0);
                    float specular = pow(specAngle, shininess);
                    
                
                    vec3 final_color = ambientLight + 
                                         0.8 * lambert1 * color + 
                                         1.0 * lambert2 * color;
                
                    f_color = vec4(final_color, 1.0);
                }
                '''
geometry_shader = '''
                #version 330 core
                
                layout(triangles) in;
                layout(triangle_strip, max_vertices = 3) out;
                
                out vec3 out_normal;
                
                void main() {
                    vec4 edge1 = gl_in[0].gl_Position - gl_in[1].gl_Position;
                    vec4 edge2 = gl_in[0].gl_Position - gl_in[2].gl_Position;
                    out_normal = cross(edge1.xyz, edge2.xyz);
                    
                    for(int i = 0; i < 3; i++) {
                        gl_Position = gl_in[i].gl_Position;
                        EmitVertex();
                    }
                    EndPrimitive();
                }
                '''


class HandRenderer:
    def __init__(self, image_size=128, num_vertices=778):
        """
        Class for rendering a hand from parameters or manifold
        :param image_size: size of a single hand image
        """

        self.image_size = image_size
        self.num_vertices = num_vertices

        # graphics
        self.ctx = moderngl.create_standalone_context()

        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.enable(moderngl.CULL_FACE)

        self.prog = self.ctx.program(
            vertex_shader=vertex_shader,
            fragment_shader=fragment_shader,
            geometry_shader=geometry_shader
        )

        self.vboPos = self.ctx.buffer(reserve=num_vertices * 3 * 4, dynamic=True)

        self.ibo = self.ctx.buffer(get_mano_faces().astype('i4').tobytes())

        vao_content = [
            # 3 floats are assigned to the 'in' variable named 'in_vert' in the shader code
            (self.vboPos, '3f', 'in_vert')
        ]

        self.vao = self.ctx.vertex_array(self.prog, vao_content, self.ibo)

        # Framebuffers
        self.fbo1 = self.ctx.framebuffer([self.ctx.renderbuffer((image_size, image_size), samples=8)])
        self.fbo2 = self.ctx.framebuffer([self.ctx.renderbuffer((image_size, image_size))])

    def __del__(self):

        self.prog.release()
        self.vboPos.release()
        self.ibo.release()
        self.vao.release()
        self.fbo1.release()
        self.fbo2.release()

    def render_manifold(self, decoder, filename="./manifold.png", bounds=(-4, 4), num_samples=16, color=(1, 0, 0),
                        verbose=False):
        """
        Render a 2D posed hand manifold
        :param decoder: pytorch decoder function 2 -> 45 params, should be called with torch.no_grad():
        :param filename: filename
        :param bounds: bounds of the sampling along the x and y axis
        :param num_samples: number of samples between the bounds in a row
        :param color: color of rendered model
        :param verbose: print progress
        :returns rendered image
        """

        steps = (bounds[1] - bounds[0]) / num_samples

        # coordinates to sample at
        sampling_grid = np.mgrid[bounds[0]:bounds[1]:steps, bounds[0]:bounds[1]:steps]
        encoded = sampling_grid.reshape(2, -1).T

        _, cols, rows = sampling_grid.shape

        encoded = torch.tensor(encoded, dtype=torch.float).cuda()
        batch_size = len(encoded)

        decoded_poses = decoder(encoded)
        decoded_poses = decoded_poses.cpu().detach().numpy() + mano_data['hands_mean']

        rot = np.zeros([batch_size, 3])
        rot[:, 0] = np.pi / 4

        poses = np.concatenate((rot, decoded_poses), axis=1)
        shapes = np.zeros([batch_size, 10])

        vertices = get_mano_vertices(shapes, poses)

        self.render_hands(vertices, dims=(cols, rows), filename=filename, color=color, verbose=verbose)

    def render_hands(self, vertices, dims, filename="./manifold.png", color=(1, 0, 0), verbose=False):
        batch_size = len(vertices)

        cols, rows = dims
        result_length = self.image_size * cols

        res = Image.new("RGB", (int(result_length), int(result_length)))
        for x in range(cols):
            for y in range(rows):
                if verbose:
                    print("Rendering at {x}, {y}".format(x=x, y=y))

                model_index = y * rows + x

                if model_index > batch_size:
                    continue

                img = self.render_mano(vertices[model_index], color=color)
                if verbose:
                    draw = ImageDraw.Draw(img)
                    draw.text((0, 0), f"{model_index}", fill=(0, 0, 0), font=ImageFont.truetype("arial"))

                x_pos = x * self.image_size
                y_pos = y * self.image_size

                res.paste(img, (int(x_pos), int(y_pos)))
        if verbose:
            print("Manifold rendered")

        if filename is not None:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            res.save(filename)
        return res

    def render_mano(self, vertices, index=None, color=(1, 0, 0)):
        """
        Render Mano on a single image
        :param vertices: vertices of model render
        :param color:
        :return image of the hand
        """
        vertices = vertices * 10.
        vertices[:] -= [0.15, 0.0, 0.0]

        self.vboPos.write(vertices.astype('f4').tobytes())

        self.prog['color'].value = color

        # Rendering
        self.fbo1.use()
        self.ctx.clear(0.9, 0.9, 0.9)
        self.vao.render()

        # Downsampling and loading the image using Pillow
        self.ctx.copy_framebuffer(self.fbo2, self.fbo1)
        data = self.fbo2.read(components=3, alignment=1)
        img = Image.frombytes('RGB', self.fbo2.size, data)  # .transpose(Image.FLIP_TOP_BOTTOM)

        # img.show()
        return img


if __name__ == '__main__':
    renderer = HandRenderer(image_size=256)

    # rendering mano
    pose = np.zeros([1, 48])
    finger = 0
    joint = 0
    i = 3 + 3 * finger + joint
    # pose[0, i + 1] = np.pi / 2
    pose[0, 0] = np.pi / 2

    from block_timer.timer import Timer

    poses = get_mano_vertices(np.zeros([1, 10]), pose)

    with Timer():
        img = renderer.render_mano(poses[0])

    plt.imsave('rendering_test.png', img)

    # rendering manifold
    from pose_autoencoders.vanilla_ae import autoencoder

    ae = autoencoder().cuda()  # Load a premade autoencoder
    ae.load_state_dict(torch.load('../pose_autoencoders/sim_autoencoder.pth'))

    with Timer():
        renderer.render_manifold(ae.decoder, filename='./manifold_test.png', verbose=False)
